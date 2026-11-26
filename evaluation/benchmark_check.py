"""Generic benchmark receipt checks and strictly gated formal acceptance.

Draft receipts can describe valid partial contributions. They do not assert
independent approval, full-range equivalence, or a completed Experiment A.
"""

import argparse
from collections import Counter, defaultdict
import hashlib
from itertools import permutations
import json
import math
from pathlib import Path
import re

import duckdb
import sqlglot
from sqlglot import exp

from .author_checks import structural_signature
from .duckdb_oracle import BIGINT_MAX, BIGINT_MIN, bounded_schema, observe_row, schema_columns, validate_pair
from .production import ProductionUnavailable, load_production
from .provenance import production_provenance
from .sample_check import DEFAULT_SEEDS, ROOT, check_task
from .verifier_check import check_result_errors, dependency_versions, load_fixtures, validation_result_errors
from .verifier_probes import CALIBRATION_CASES, PROBE_QUERY, PROBE_SCHEMA


FAMILIES = tuple(f"{category}{number}" for category in "ABCDE" for number in range(1, 6))
TASK_IDS = tuple(f"{family}-{variant:02d}" for family in FAMILIES for variant in range(1, 5))
DEV_FAMILIES = {"A2", "B3", "C1", "D1", "E2"}
OWNERS = {family: "llm" if family.startswith("A") or family in {"B1", "B2", "D1", "D2"}
          else "verifier" if family in {"D3", "D4", "D5"} or family.startswith("E")
          else "evaluation" for family in FAMILIES}
REVIEWERS = {"llm": "verifier", "verifier": "evaluation", "evaluation": "llm"}
TASK_FIELDS = {"task_id", "family", "split", "schema_sql", "original_sql", "reference_sql", "sample_rows", "expected_rows", "derivation", "source", "error_exposed"}
REVIEW_FIELDS = {"task_id", "author_role", "reviewer_role", "review_status", "review_notes", "check_removal_test"}
REMOVAL_FIELDS = {"removed_check_sql", "schema_without_check_sql", "row", "original_truth", "reference_truth", "original_rows", "reference_rows"}


class ScreeningIncomplete(ValueError):
    """An expensive structural screen needs explicit independent resolution."""


def _need(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _rows(value):
    return isinstance(value, list) and all(isinstance(row, list) and bool(row) and all(type(item) is int for item in row) for row in value)


def _json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            _need(key not in result, f"Duplicate JSON object key: {key}")
            result[key] = value
        return result

    def reject(value):
        raise ValueError(f"Nonstandard JSON constant: {value}")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=reject)


def _add(issues, code, message, *, severity="error", **details):
    issues.append({"code": code, "message": message, "severity": severity, **details})


def _file_hash(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest() if path is not None else None
    except OSError:
        return None


def _read_lines(path, issues, kind):
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as error:
        _add(issues, f"{kind}_READ_ERROR", str(error))
        return []
    records = []
    for line, text in enumerate(text.splitlines(), 1):
        if text.strip():
            try:
                records.append((_json(text), line))
            except (ValueError, TypeError, RecursionError) as error:
                _add(issues, f"{kind}_JSON_ERROR", str(error), line=line)
    return records


def _parse(sql):
    _need(_text(sql), "SQL must be nonempty text")
    nodes = [node for node in sqlglot.parse(sql, read="duckdb") if node is not None and not isinstance(node, exp.Semicolon)]
    _need(len(nodes) == 1, "Expected exactly one SQL statement")
    return nodes[0]


def _canonical(node):
    return node.sql(dialect="duckdb", normalize=True, comments=False)


def _task(task):
    _need(isinstance(task, dict) and TASK_FIELDS.issubset(task), "Task needs every shared base-record field")
    _need(all(_text(task[name]) for name in TASK_FIELDS - {"sample_rows", "expected_rows"}), "Task IDs, SQL and reasoning must be nonempty text")
    _need(task["task_id"] in TASK_IDS, "Task ID must be a canonical family-01 through family-04 ID")
    family = task["task_id"].split("-")[0]
    _need(task["family"] == family, "Task ID and family disagree")
    _need(task["split"] == ("dev" if family in DEV_FAMILIES else "test"), "Task split disagrees with the canonical family split")
    _need(isinstance(task["sample_rows"], list) and bool(task["sample_rows"]), "sample_rows must be a nonempty array")
    for row in task["sample_rows"]:
        _need(isinstance(row, dict) and bool(row) and all(isinstance(name, str) and type(value) is int for name, value in row.items()), "Sample rows must be complete integer objects, excluding booleans and nulls")
        _need(all(BIGINT_MIN <= value <= BIGINT_MAX for value in row.values()), "Benchmark samples must be legal signed BIGINT data")
    _need(_rows(task["expected_rows"]), "expected_rows must be projected integer row arrays")
    _need(validate_pair(task["schema_sql"], task["original_sql"], task["reference_sql"]) is None, "Task SQL is unsupported or the reference changes the fixed table/projection")


def _review(review):
    _need(isinstance(review, dict) and REVIEW_FIELDS.issubset(review), "Review needs every shared review field")
    task_id = review["task_id"]
    _need(isinstance(task_id, str) and task_id in TASK_IDS, "Review task_id is not canonical")
    owner = OWNERS[task_id.split("-")[0]]
    _need(review["author_role"] == owner, f"The family belongs to author role {owner}")
    _need(review["reviewer_role"] == REVIEWERS[owner], f"The required independent reviewer role is {REVIEWERS[owner]}")
    _need(isinstance(review["review_status"], str) and review["review_status"] in {"PENDING", "APPROVED"}, "Unknown review_status")
    _need(review["review_notes"] is None or isinstance(review["review_notes"], str), "review_notes must be text or null")
    if review["review_status"] == "APPROVED":
        _need(_text(review["review_notes"]), "APPROVED requires nonempty independent review notes")
        if task_id.startswith("E"):
            _need(isinstance(review["check_removal_test"], dict), "An approved E-family task requires a CHECK-removal test")
    _need(review["check_removal_test"] is None or isinstance(review["check_removal_test"], dict), "check_removal_test must be an object or null")


def _check_name(check):
    parent = check.parent
    if isinstance(parent, (exp.Constraint, exp.ColumnConstraint)):
        identifier = parent.args.get("this")
        return identifier.name.casefold() if isinstance(identifier, exp.Identifier) else None
    return None


def check_removal(task, metadata):
    """Remove exactly one identified CHECK, preserving the entire remaining DDL."""
    _need(isinstance(metadata, dict) and REMOVAL_FIELDS.issubset(metadata), "CHECK-removal metadata is incomplete")
    _need(_text(metadata["removed_check_sql"]) and _text(metadata["schema_without_check_sql"]), "CHECK removal needs the exact clause and reduced schema")
    original = _parse(task["schema_sql"])
    selector = _parse("CREATE TABLE removal_selector (placeholder BIGINT NOT NULL, " + metadata["removed_check_sql"] + ")")
    selected_checks = list(selector.find_all(exp.CheckColumnConstraint))
    _need(len(selector.this.expressions) == 2 and len(selected_checks) == 1, "removed_check_sql must identify exactly one CHECK clause")
    clause = selector.this.expressions[1]
    _need(isinstance(clause, (exp.CheckColumnConstraint, exp.Constraint)), "removed_check_sql must be a CHECK clause, not a column definition or another schema element")
    if isinstance(clause, exp.Constraint):
        _need(len(clause.expressions) == 1 and isinstance(clause.expressions[0], exp.CheckColumnConstraint), "A named removal selector must wrap exactly one CHECK clause")
    selected, name = selected_checks[0], _check_name(selected_checks[0])
    reduced = original.copy()
    matches = [check for check in reduced.find_all(exp.CheckColumnConstraint)
               if _canonical(check) == _canonical(selected) and (name is None or _check_name(check) == name)]
    _need(len(matches) == 1, "Removed CHECK is missing or ambiguous; identify a unique named CHECK occurrence")
    check = matches[0]
    parent = check.parent
    if isinstance(parent, exp.ColumnConstraint):
        parent.pop()
    elif isinstance(parent, exp.Constraint):
        check.pop()
        if not parent.expressions:
            parent.pop()
    else:
        check.pop()
    supplied = _parse(metadata["schema_without_check_sql"])
    _need(_canonical(reduced) == _canonical(supplied), "Reduced schema must differ only by removal of that single CHECK; all other constraints and columns must remain unchanged")
    schema_columns(metadata["schema_without_check_sql"])
    row = metadata["row"]
    _need(isinstance(row, dict) and bool(row) and all(isinstance(key, str) and type(value) is int for key, value in row.items()), "Removal witness must contain complete integer row data")
    _need(type(metadata["original_truth"]) is bool and type(metadata["reference_truth"]) is bool and metadata["original_truth"] != metadata["reference_truth"], "Removal witness needs different Boolean truths")
    _need(_rows(metadata["original_rows"]) and _rows(metadata["reference_rows"]), "Removal witness needs projected integer arrays")
    original_schema = observe_row(task["schema_sql"], task["original_sql"], row)
    _need(original_schema["status"] == "REJECTED_CHECK", "A CHECK-dependence witness must violate the removed CHECK under the original schema")
    observations = {}
    for source, field in (("original", "original_sql"), ("reference", "reference_sql")):
        observation = observe_row(metadata["schema_without_check_sql"], task[field], row)
        _need(observation["status"] == "OBSERVED" and observation["schema_holds"] is True, "Removal witness violates a remaining constraint or cannot be executed independently")
        _need(observation["predicate_truth"] == metadata[f"{source}_truth"], f"Declared {source} truth disagrees with DuckDB")
        _need(Counter(map(tuple, observation["rows"])) == Counter(map(tuple, metadata[f"{source}_rows"])), f"Declared {source} projected multiset disagrees with DuckDB")
        observations[source] = observation
    _need(observations["original"]["predicate_truth"] != observations["reference"]["predicate_truth"], "Removing the CHECK does not distinguish the predicates")
    _need(Counter(map(tuple, observations["original"]["rows"])) != Counter(map(tuple, observations["reference"]["rows"])), "Removing the CHECK does not distinguish projected outputs")
    return {"passed": True, "removed_check_sql": metadata["removed_check_sql"], "schema_without_check_sql": metadata["schema_without_check_sql"],
            "row": row, "original_schema": original_schema, **observations}


def _structure(task):
    """Screen joint predicates and CHECKs, retaining shared literal relationships."""
    schema, query = _parse(task["schema_sql"]), _parse(task["original_sql"])
    checks = [check.this for check in schema.find_all(exp.CheckColumnConstraint)]
    if not checks:
        if len({column.name.casefold() for column in query.args["where"].this.find_all(exp.Column)}) > 6:
            raise ScreeningIncomplete("More than six predicate columns require an independent structural-duplicate review; no clean screen is claimed")
        return "plain:" + structural_signature(task["original_sql"])
    predicate = query.args["where"].this
    columns, literals = set(), set()

    def signed(node):
        if isinstance(node, exp.Literal):
            return int(node.this)
        if isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal):
            return -int(node.this.this)
        return None

    def collect(node):
        if isinstance(node, exp.Column):
            columns.add(node.name.casefold())
        elif signed(node) is not None:
            literals.add(signed(node))
            return
        for child in node.iter_expressions():
            collect(child)

    for node in [predicate, *checks]:
        collect(node)
    values = sorted(literals)
    ranks = {value: index for index, value in enumerate(values)}
    roles = ["MIN" if value == BIGINT_MIN else "MAX" if value == BIGINT_MAX else "ordinary" for value in values]
    adjacency = [right - left == 1 for left, right in zip(values, values[1:])]
    inverse = {exp.EQ: "EQ", exp.NEQ: "NEQ", exp.LT: "GT", exp.GT: "LT", exp.LTE: "GTE", exp.GTE: "LTE"}

    def canonical(node, names):
        if isinstance(node, exp.Paren):
            return canonical(node.this, names)
        if isinstance(node, exp.Column):
            return ("column", names[node.name.casefold()])
        if signed(node) is not None:
            return ("integer", ranks[signed(node)])
        if isinstance(node, exp.Boolean):
            return ("boolean", bool(node.this))
        if type(node) in inverse:
            left, right = canonical(node.this, names), canonical(node.expression, names)
            return min((type(node).__name__.upper(), left, right), (inverse[type(node)], right, left))
        if isinstance(node, exp.Not):
            return ("NOT", canonical(node.this, names))
        _need(type(node) in (exp.And, exp.Or), "Unsupported expression during structural screening")
        parts = []

        def flatten(child):
            if isinstance(child, exp.Paren):
                flatten(child.this)
            elif type(child) is type(node):
                flatten(child.this)
                flatten(child.expression)
            else:
                parts.append(canonical(child, names))

        flatten(node.this)
        flatten(node.expression)
        return (type(node).__name__.upper(), tuple(sorted(parts)))

    if len(columns) > 6:
        raise ScreeningIncomplete("More than six joint predicate/CHECK columns require an independent structural-duplicate review; no clean screen is claimed")
    orderings = permutations(sorted(columns))
    signatures = []
    for ordering in orderings:
        names = {name: index for index, name in enumerate(ordering)}
        signatures.append(json.dumps((roles, adjacency, canonical(predicate, names), sorted(canonical(check, names) for check in checks))))
    return "constrained:" + min(signatures)


def _seed_blocks(path):
    text = Path(path).read_text(encoding="utf-8")
    headings = list(re.finditer(r"^--\s+([A-E][1-5])\.\s+.*$", text, re.MULTILINE))
    blocks = {}
    with duckdb.connect(":memory:") as connection:
        for index, heading in enumerate(headings):
            family = heading[1]
            _need(family not in blocks, f"Duplicate seed family: {family}")
            block = text[heading.start():headings[index + 1].start() if index + 1 < len(headings) else len(text)]
            statements = connection.extract_statements(block)
            ddl = [statement.query for statement in statements if statement.type.name == "CREATE"]
            queries = [statement.query for statement in statements if statement.type.name == "SELECT"]
            _need(len(ddl) == 1 and len(queries) == 2, f"Seed {family} must contain one DDL and two SELECTs")
            blocks[family] = tuple(_canonical(_parse(sql)) for sql in ddl + queries)
    return blocks


def _evidence(path, provenance, versions):
    _need(path is not None, "A successful current Experiment A report is required")
    report = _json(Path(path).read_text(encoding="utf-8-sig"))
    _need(isinstance(report, dict), "Experiment A evidence must be a report object")
    _need(report.get("mode") == "production" and type(report.get("exit_code")) is int and report["exit_code"] == 0,
          "Experiment A must be an actual successful production run, not an offline/test report")
    _need(report.get("test_standin") is False, "Experiment A must explicitly exclude test stand-ins")
    _need(report.get("dependency_versions") == versions, "Experiment A dependency versions do not match this environment")
    recorded = report.get("verifier_provenance")
    _need(provenance.get("verified") is True and isinstance(recorded, dict) and recorded.get("verified") is True and recorded == provenance,
          "Experiment A verifier provenance does not match the current source-backed production implementation")
    fixture = ROOT / "evaluation/verifier_cases.jsonl"
    _need(report.get("fixture_sha256") == hashlib.sha256(fixture.read_bytes()).hexdigest(), "Experiment A fixture fingerprint does not match the current independent fixtures")
    offline, production = report.get("offline"), report.get("production")
    _need(isinstance(offline, dict) and isinstance(production, dict), "Experiment A is missing offline or production evidence")
    summary, metrics = offline.get("summary"), production.get("metrics")
    _need(isinstance(summary, dict) and isinstance(metrics, dict), "Experiment A is missing coverage metrics")
    _need(production.get("run_status") == "COMPLETED" and production.get("discrepancies") == [], "Experiment A is incomplete or has unresolved discrepancies")
    fixtures = load_fixtures(fixture)
    expected_cases = len(fixtures)
    expected_bounded = sum(case["enumeration"] is not None for case in fixtures)
    expected_rows = sum(math.prod(len(values) for values in case["enumeration"]["domains"].values()) for case in fixtures if case["enumeration"] is not None)
    expected_replays = sum(case["expected_status"] == "DIFFERENT" for case in fixtures)
    for key, expected in (("fixtures", expected_cases), ("failed_cases", 0), ("type_bound_probes", 8), ("type_bound_probe_errors", 0)):
        _need(type(summary.get(key)) is int and summary[key] == expected, f"Experiment A offline metric {key} is missing or inconsistent")
    for name, expected in (("enumerated_rows", expected_rows), ("bounded_cases", expected_bounded),
                           ("independent_replays", expected_replays), ("confirmed_independent_replays", expected_replays)):
        _need(type(summary.get(name)) is int and summary[name] == expected, f"Experiment A {name} must cover the current fixtures exactly")
    _need(summary["independent_replays"] == summary["confirmed_independent_replays"], "Experiment A independent witness replay was incomplete")
    counts = {"full_checks": expected_cases, "bounded_checks": summary["bounded_cases"], "row_checks": 2 * summary["enumerated_rows"],
              "type_bound_probes": 8, "calibration_cases": 3, "calibration_checks": 3, "calibration_validation_checks": 3}
    for name, expected in counts.items():
        _need(type(metrics.get(name)) is int and metrics[name] == expected, f"Experiment A production coverage {name} is incomplete")
    for name in ("false_equivalent", "false_different", "verdict_mismatches", "validation_mismatches", "validation_inconclusive", "inconclusive_checks",
                 "row_mismatches", "row_inconclusive", "row_errors", "replay_errors", "infrastructure_errors", "node_count_mismatches",
                 "type_bound_mismatches", "type_bound_errors", "type_bound_inconclusive", "calibration_node_mismatches", "calibration_errors"):
        _need(type(metrics.get(name)) is int and metrics[name] == 0, f"Experiment A has missing or unresolved {name}")
    for prefix in ("", "calibration_"):
        _need(type(metrics.get(prefix + "sat_disagreement_models")) is int and metrics[prefix + "sat_disagreement_models"] > 0,
              "Experiment A needs actual SAT disagreement replay coverage")
        _need(type(metrics.get(prefix + "confirmed_replays")) is int and metrics[prefix + "confirmed_replays"] == metrics[prefix + "sat_disagreement_models"]
              and type(metrics.get(prefix + "confirmed_replay_rate")) in (int, float) and metrics[prefix + "confirmed_replay_rate"] == 1,
              "Experiment A has unconfirmed disagreement models")
    _evidence_details(report, fixtures)
    return {"accepted": True, "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(), "verifier_fingerprint": provenance["fingerprint"]}


def _indexed(records, expected_ids, key, label):
    _need(isinstance(records, list) and len(records) == len(expected_ids), f"Experiment A needs all {label} detail records")
    _need(all(isinstance(record, dict) and isinstance(record.get(key), str) for record in records), f"Malformed Experiment A {label} records")
    indexed = {record[key]: record for record in records}
    _need(len(indexed) == len(records) and set(indexed) == set(expected_ids), f"Experiment A {label} identities must match the current fixtures exactly")
    return indexed


def _evidence_details(report, fixtures):
    """Require current fixture, Python probe and calibration detail coverage."""
    fixture_ids = {case["case_id"] for case in fixtures}
    offline = _indexed(report["offline"].get("cases"), fixture_ids, "case_id", "offline case")
    production = _indexed(report["production"].get("cases"), fixture_ids, "case_id", "production case")
    total_legal = total_distinguishing = 0
    for case in fixtures:
        independent, actual = offline[case["case_id"]], production[case["case_id"]]
        _need(independent.get("expected_status") == case["expected_status"] and independent.get("errors") == [], "Experiment A case labels or independent errors disagree with current fixtures")
        validation, full = actual.get("validation"), actual.get("full_range")
        _need(not validation_result_errors(validation), "Experiment A has a malformed production ValidationResult")
        source_status = "INVALID_INPUT" if case["expected_status"] == "INVALID_INPUT" else "UNSAT_SCHEMA" if case["expected_status"] == "UNSAT_SCHEMA" else "READY"
        _need(validation["status"] == source_status, "Experiment A preflight outcome disagrees with the current fixture")
        _need(isinstance(full, dict) and not check_result_errors(full) and full["status"] == case["expected_status"], "Experiment A full-range result does not match the current fixture")
        if full["status"] in ("EQUIVALENT", "DIFFERENT"):
            _need(full["original_nodes"] == validation["original_nodes"], "Experiment A node counts disagree between preflight and verification")
        if case["expected_status"] == "DIFFERENT":
            replay = independent.get("counterexample_replay")
            _need(isinstance(replay, dict) and replay.get("confirmed") is True, "Experiment A is missing an independently confirmed fixture counterexample")
        if case["enumeration"] is None:
            _need(independent.get("bounded") is None and actual.get("bounded") is None, "Unsupported controls must not claim finite-domain results")
            continue
        bounded = independent.get("bounded")
        expected_rows = math.prod(len(values) for values in case["enumeration"]["domains"].values())
        _need(isinstance(bounded, dict) and bounded.get("errors") == [] and type(bounded.get("total_rows")) is int and bounded["total_rows"] == expected_rows,
              "Experiment A bounded detail does not exhaust the current recorded Cartesian domain")
        for name in ("legal_rows", "distinguishing_rows"):
            _need(type(bounded.get(name)) is int and 0 <= bounded[name] <= expected_rows, f"Invalid Experiment A bounded {name}")
        _need(bounded["distinguishing_rows"] <= bounded["legal_rows"], "Distinguishing rows must be legal rows")
        bounded_status = "UNSAT_SCHEMA" if bounded["legal_rows"] == 0 else "DIFFERENT" if bounded["distinguishing_rows"] else "EQUIVALENT"
        _need(bounded.get("status") == bounded_status, "Experiment A bounded status disagrees with its observations")
        if case["expected_status"] in ("EQUIVALENT", "UNSAT_SCHEMA"):
            _need(bounded_status == case["expected_status"], "Experiment A bounded result contradicts its unrestricted fixture")
        total_legal += bounded["legal_rows"]
        total_distinguishing += bounded["distinguishing_rows"]
        checked = actual.get("bounded")
        _need(isinstance(checked, dict) and checked.get("expected_status") == bounded_status, "Experiment A is missing its bounded production comparison")
        _need(_canonical(_parse(checked.get("schema_sql"))) == _canonical(_parse(bounded_schema(case["schema_sql"], case["enumeration"]))), "Experiment A bounded schema did not retain the exact domain and every fixed column")
        result = checked.get("result")
        _need(isinstance(result, dict) and not check_result_errors(result) and result["status"] == bounded_status, "Experiment A bounded verifier outcome is incomplete or mismatched")
        if result["status"] in ("EQUIVALENT", "DIFFERENT"):
            _need(result["original_nodes"] == validation["original_nodes"], "Experiment A bounded counts disagree with the original preflight")
    summary = report["offline"]["summary"]
    _need(type(summary.get("legal_rows")) is int and summary["legal_rows"] == total_legal and type(summary.get("distinguishing_rows")) is int and summary["distinguishing_rows"] == total_distinguishing,
          "Experiment A row totals do not reconcile with its per-case evidence")
    probes = {f"type-bound-{column}-{boundary}": (column, value) for column in ("row_id", "x")
              for boundary, value in (("below_minimum", BIGINT_MIN - 1), ("minimum", BIGINT_MIN), ("maximum", BIGINT_MAX), ("above_maximum", BIGINT_MAX + 1))}
    offline_probes = report["offline"].get("type_bound_probes")
    actual_probes = report["production"].get("type_bound_probes")
    _need(isinstance(offline_probes, dict) and offline_probes.get("errors") == [] and type(offline_probes.get("probe_count")) is int and offline_probes["probe_count"] == 8, "Experiment A needs all independent Python type-bound expectations")
    _need(isinstance(actual_probes, dict) and actual_probes.get("run_status") == "COMPLETED" and actual_probes.get("discrepancies") == [], "Experiment A type-bound comparisons are incomplete")
    independent = _indexed(offline_probes.get("probes"), probes, "probe_id", "independent type-bound probe")
    actual = _indexed(actual_probes.get("probes"), probes, "probe_id", "production type-bound probe")
    for probe_id, (column, value) in probes.items():
        expected, checked = independent[probe_id], actual[probe_id]
        row = {"row_id": 0, "x": 0}
        row[column] = value
        legal = BIGINT_MIN <= value <= BIGINT_MAX
        _need(expected.get("row") == row and expected.get("column") == column and type(expected.get("value")) is int and expected["value"] == value,
              "Experiment A type-bound probe data differs from the required fixed Python rows")
        _need(expected.get("schema_sql") == PROBE_SCHEMA and expected.get("query_sql") == PROBE_QUERY, "Experiment A type-bound probes must use the minimal unconstrained table and fixed query")
        observation = expected.get("expected")
        _need(isinstance(observation, dict) and observation.get("status") == ("OBSERVED" if legal else "REJECTED_TYPE") and observation.get("schema_holds") is legal,
              "Experiment A type-bound expected legality is inconsistent")
        _need(checked.get("row") == row and checked.get("expected") == observation and checked.get("matched") is True, "Experiment A type-bound comparison details do not reconcile")
        encoded = checked.get("actual")
        _need(isinstance(encoded, dict) and encoded.get("status") == "EVALUATED" and encoded.get("schema_holds") is legal and type(encoded.get("predicate_truth")) is bool and encoded.get("diagnostic") is None,
              "Experiment A type-bound production observation is missing or mismatched")
        if legal:
            _need(encoded["predicate_truth"] == observation.get("predicate_truth") == (row["x"] >= 0), "Experiment A legal type-bound predicate observation is mismatched")
        else:
            _need(observation.get("predicate_truth") is None, "An illegal inserted row must not claim a DuckDB predicate observation")
    calibration = report["production"].get("calibration")
    _need(isinstance(calibration, dict) and calibration.get("run_status") == "COMPLETED" and calibration.get("discrepancies") == [], "Experiment A node calibration is incomplete")
    records = _indexed(calibration.get("cases"), {case["case_id"] for case in CALIBRATION_CASES}, "case_id", "node calibration")
    for case in CALIBRATION_CASES:
        record = records[case["case_id"]]
        validation, result = record.get("validation"), record.get("result")
        _need(not validation_result_errors(validation) and validation["status"] == "READY" and validation["original_nodes"] == case["original_nodes"], "Experiment A calibration preflight counts are inconsistent")
        _need(isinstance(result, dict) and not check_result_errors(result) and result["status"] == case["expected_status"] and
              all(result[name] == case[name] for name in ("original_nodes", "candidate_nodes")), "Experiment A calibration verdict or known node counts are inconsistent")
        request = record.get("request")
        _need(isinstance(request, dict) and all(request.get(name) == case[name] for name in ("schema_sql", "original_sql", "candidate_sql")) and
              type(request.get("timeout_ms")) is int and request["timeout_ms"] == report.get("timeout_ms"), "Experiment A calibration request provenance is inconsistent")


def validate_benchmark(manifest, *, reviews=None, mode="draft", seed_file=DEFAULT_SEEDS, experiment_a_report=None, api=None, timeout_ms=5000):
    """Validate generic contributions; injected APIs are explicitly test-only."""
    issues, tasks, review_records = [], {}, {}
    versions = dependency_versions()
    report = {"mode": mode, "status": "INVALID", "exit_code": 1, "dependency_versions": versions, "timeout_ms": timeout_ms, "issues": issues,
              "sample_checks": [], "seed_checks": [], "check_removal_checks": [], "duplicate_signatures": [],
              "duplicate_screen": {"method": "Existing author_checks.structural_signature for unconstrained predicates; shared column/literal context for predicates with schema CHECKs",
                                   "scope": "Normalization heuristic over names, commuted operands, literal relationships and BIGINT endpoint roles; flagged task IDs require independent review of schema and boundary intent"},
              "production": {"run_status": "NOT_RUN", "test_standin": api is not None, "checks": [], "missing_interfaces": []},
              "experiment_a_evidence": {"accepted": False}}
    if mode not in ("draft", "formal") or type(timeout_ms) is not int or timeout_ms <= 0:
        _add(issues, "INVALID_OPTIONS", "mode must be draft/formal and timeout_ms must be a positive integer")
        return report
    manifest = Path(manifest)
    review_path = Path(reviews) if reviews is not None else manifest.with_name("benchmark_reviews.jsonl")
    report["input_sha256"] = {"manifest": _file_hash(manifest), "reviews": _file_hash(review_path),
                              "seed_file": _file_hash(seed_file), "experiment_a_report": _file_hash(experiment_a_report)}
    for task, line in _read_lines(manifest, issues, "MANIFEST"):
        task_id = task.get("task_id") if isinstance(task, dict) and isinstance(task.get("task_id"), str) else None
        try:
            _task(task)
            _need(task["task_id"] not in tasks, "Duplicate task_id in manifest")
            tasks[task["task_id"]] = task
        except Exception as error:
            _add(issues, "INVALID_TASK", str(error), task_id=task_id, line=line)
    if not tasks:
        _add(issues, "EMPTY_MANIFEST", "The manifest contains no valid task records")
    if review_path.exists() or reviews is not None:
        for review, line in _read_lines(review_path, issues, "REVIEWS"):
            task_id = review.get("task_id") if isinstance(review, dict) and isinstance(review.get("task_id"), str) else None
            try:
                _review(review)
                _need(review["task_id"] not in review_records, "Duplicate review task_id")
                review_records[review["task_id"]] = review
            except Exception as error:
                _add(issues, "INVALID_REVIEW", str(error), task_id=task_id, line=line)
    blocks = None
    if seed_file is not None:
        try:
            blocks = _seed_blocks(seed_file)
        except Exception as error:
            _add(issues, "SEED_READ_ERROR", str(error))
    elif mode == "formal":
        _add(issues, "MISSING_SEEDS", "Formal acceptance requires the original SQL seeds", severity="blocker")
    groups = defaultdict(list)
    for task_id, task in sorted(tasks.items()):
        try:
            groups[_structure(task)].append(task_id)
        except ScreeningIncomplete as error:
            report["duplicate_screen"].setdefault("incomplete_ids", []).append(task_id)
            _add(issues, "DUPLICATE_SCREEN_INCOMPLETE", str(error), task_id=task_id, severity="blocker" if mode == "formal" else "warning")
        except Exception as error:
            _add(issues, "STRUCTURAL_SCREEN_ERROR", str(error), task_id=task_id)
        try:
            if task_id.endswith("-01") and blocks is not None:
                actual = tuple(_canonical(_parse(task[field])) for field in ("schema_sql", "original_sql", "reference_sql"))
                _need(blocks.get(task["family"]) == actual, "Variant 01 must retain its seed DDL, original SQL and reference SQL")
                report["seed_checks"].append({"task_id": task_id, "passed": True})
            checks = check_task(task, seed_file=seed_file if task_id.endswith("-01") and blocks is not None else None)
            for check in checks:
                detail = {"task_id": task_id, "source": check.source, "passed": check.passed, "actual_rows": list(map(list, check.actual_rows)),
                          "missing": [{"row": list(row), "count": count} for row, count in sorted(check.missing.items())],
                          "unexpected": [{"row": list(row), "count": count} for row, count in sorted(check.unexpected.items())], "error": check.error}
                report["sample_checks"].append(detail)
                if not check.passed:
                    _add(issues, "SAMPLE_MISMATCH", f"{check.source} disagrees with seed inputs or author expected output", task_id=task_id, observation=detail)
        except Exception as error:
            _add(issues, "SEED_MISMATCH" if task_id.endswith("-01") else "TASK_CHECK_ERROR", str(error), task_id=task_id)
        review = review_records.get(task_id)
        if review is not None and review["check_removal_test"] is not None:
            try:
                report["check_removal_checks"].append({"task_id": task_id, **check_removal(task, review["check_removal_test"])})
            except Exception as error:
                _add(issues, "CHECK_REMOVAL_ERROR", str(error), task_id=task_id)
    for ids in groups.values():
        if len(ids) > 1:
            report["duplicate_signatures"].append(ids)
            _add(issues, "DUPLICATE_STRUCTURE", "Normalized original predicates and schema constraints duplicate existing task coverage", task_ids=ids)
    missing = [task_id for task_id in TASK_IDS if task_id not in tasks]
    pending = [task_id for task_id in sorted(tasks) if task_id not in review_records or review_records[task_id]["review_status"] != "APPROVED"]
    report["coverage"] = {"received": len(tasks), "expected": 100, "missing_ids": missing,
                          "families": {family: sorted(task_id for task_id in tasks if task_id.startswith(family + "-")) for family in FAMILIES},
                          "by_owner": dict(Counter(OWNERS[task["family"]] for task in tasks.values())),
                          "split_counts": dict(Counter(task["split"] for task in tasks.values()))}
    report["reviews"] = {"received": len(review_records), "pending_ids": pending,
                         "status_counts": dict(Counter(review_records[task_id]["review_status"] if task_id in review_records else "PENDING" for task_id in tasks)),
                         "unmatched_ids": sorted(set(review_records) - tasks.keys())}
    if mode == "formal":
        if missing:
            _add(issues, "INCOMPLETE_BENCHMARK", "Formal acceptance needs exactly 25 families with four canonical tasks each and the complete 20/80 split", severity="blocker", missing_ids=missing)
        if pending:
            _add(issues, "PENDING_REVIEWS", "Every formal benchmark task needs an independent APPROVED review with notes", severity="blocker", task_ids=pending)
        if set(review_records) - tasks.keys():
            _add(issues, "UNMATCHED_REVIEWS", "Formal reviews must refer to manifest tasks")
        production = api
        if api is not None:
            _add(issues, "TEST_STANDIN", "Injected test stand-ins can exercise checks but never establish formal acceptance", severity="blocker")
            if getattr(api, "__benchmark_test_standin__", False) is not True:
                _add(issues, "UNMARKED_TEST_API", "Injected APIs must declare __benchmark_test_standin__=True")
                production = None
            report["production"]["run_status"] = "TEST_ONLY" if production is not None else "NOT_RUN"
        else:
            try:
                production = load_production()
                provenance = production_provenance(production)
                report["production"]["provenance"] = provenance
                _need(provenance["verified"], provenance["diagnostic"] or "Production source provenance is unverified")
                report["experiment_a_evidence"] = _evidence(experiment_a_report, provenance, versions)
            except ProductionUnavailable as error:
                production = None
                report["production"]["missing_interfaces"] = list(error.missing)
                _add(issues, "PRODUCTION_UNAVAILABLE", str(error), severity="blocker")
            except Exception as error:
                production = None
                _add(issues, "EXPERIMENT_A_EVIDENCE", f"Current successful Experiment A evidence is required: {error}", severity="blocker")
        if not report["experiment_a_evidence"]["accepted"] and not any(issue["code"] == "EXPERIMENT_A_EVIDENCE" for issue in issues):
            try:
                provenance = report["production"].get("provenance", {"verified": False})
                report["experiment_a_evidence"] = _evidence(experiment_a_report, provenance, versions)
            except Exception as error:
                _add(issues, "EXPERIMENT_A_EVIDENCE", f"Current successful Experiment A evidence is required independently of API availability: {error}", severity="blocker")
        if production is not None:
            for task_id, task in sorted(tasks.items()):
                detail = {"task_id": task_id, "validation": None, "result": None}
                try:
                    validation = production.validate_input(task["schema_sql"], task["original_sql"], timeout_ms=timeout_ms)
                    detail["validation"] = validation
                    _need(not validation_result_errors(validation) and validation["status"] == "READY", "Production source validation must be READY")
                    result = production.verify(task["schema_sql"], task["original_sql"], task["reference_sql"], timeout_ms=timeout_ms)
                    detail["result"] = result
                    _need(isinstance(result, dict) and not check_result_errors(result), "Malformed production CheckResult")
                    _need(result["status"] == "EQUIVALENT", "Reference must be production-verified EQUIVALENT")
                    _need(result["original_nodes"] == validation["original_nodes"], "Production original node count differs from READY")
                    if not result["candidate_nodes"] < result["original_nodes"]:
                        _add(issues, "REFERENCE_NOT_SMALLER", "Reference must have strictly smaller predicate node count", task_id=task_id,
                             severity="blocker" if api is not None else "error", observation=detail)
                except Exception as error:
                    _add(issues, "REFERENCE_VERIFICATION_ERROR", str(error), task_id=task_id, severity="blocker" if api is not None else "error", observation=detail)
                report["production"]["checks"].append(detail)
            if api is None:
                report["production"]["run_status"] = "COMPLETED"
    if any(issue["severity"] == "error" for issue in issues):
        report["status"], report["exit_code"] = "INVALID", 1
    elif mode == "formal" and issues:
        report["status"], report["exit_code"] = "FORMAL_BLOCKED", 2
    else:
        report["status"], report["exit_code"] = ("DRAFT", 0) if mode == "draft" else ("FORMAL_PASSED", 0)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Receive partial drafts or validate all 100 benchmark tasks with independent approvals and actual production evidence.",
        epilog="Exit codes: 0 = valid draft receipt or fully accepted formal benchmark; 1 = malformed input or failed checks; 2 = formal acceptance blocked. A DRAFT result is not official benchmark acceptance.",
    )
    parser.add_argument("--manifest", type=Path, required=True, help="JSONL task manifest from any of the three author roles")
    parser.add_argument("--reviews", type=Path, help="Separate review JSONL; default: benchmark_reviews.jsonl beside the manifest")
    parser.add_argument("--mode", choices=("draft", "formal"), default="draft", help="Default: draft; formal additionally requires complete 100-task coverage, approval and production evidence")
    parser.add_argument("--seed-file", type=Path, default=DEFAULT_SEEDS, help="Original SQL seed file; default: data/SQL_Predicate_Family_Examples.sql")
    parser.add_argument("--experiment-a-report", type=Path, help="Successful current production check-verifier JSON report with dependency and verifier-source fingerprints")
    parser.add_argument("--timeout-ms", type=int, default=5000, help="Positive timeout applied to every production solver check; default: 5000")
    parser.add_argument("--output", type=Path, help="Write an English JSON receipt/report without changing input artifacts")
    args = parser.parse_args(argv)
    report = validate_benchmark(args.manifest, reviews=args.reviews, mode=args.mode, seed_file=args.seed_file, experiment_a_report=args.experiment_a_report, timeout_ms=args.timeout_ms)
    print(f"{report['status']}: received={report.get('coverage', {}).get('received', 0)}/100; missing={len(report.get('coverage', {}).get('missing_ids', TASK_IDS))}; pending={len(report.get('reviews', {}).get('pending_ids', []))}; formal_acceptance={str(report['status'] == 'FORMAL_PASSED').lower()}")
    coverage = report.get("coverage", {})
    families = coverage.get("families", {})
    received_families = [f"{family}:{len(ids)}/4" for family, ids in families.items() if ids]
    print("Received families: " + (", ".join(received_families) or "none") + "; owners=" + json.dumps(coverage.get("by_owner", {}), sort_keys=True))
    checks = report["sample_checks"]
    print(f"Independent sample checks: {sum(check['passed'] for check in checks)}/{len(checks)} passed; seed SQL triples={len(report['seed_checks'])}; duplicate groups={len(report['duplicate_signatures'])}")
    print("Pending review IDs: " + (", ".join(report.get("reviews", {}).get("pending_ids", [])) or "none") + "; missing families: " + (", ".join(family for family, ids in families.items() if not ids) or "none"))
    for issue in report["issues"]:
        print(f"{issue['severity'].upper()} {issue['code']}: {issue['message']}")
    if args.output is not None:
        protected = {Path(args.manifest).resolve(), Path(args.reviews).resolve() if args.reviews is not None else Path(args.manifest).with_name("benchmark_reviews.jsonl").resolve(), args.seed_file.resolve()}
        if args.experiment_a_report is not None:
            protected.add(args.experiment_a_report.resolve())
        if args.output.resolve() in protected:
            print("ERROR: report output must not overwrite an input artifact")
            return 1
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())

"""Independent fixtures, DuckDB evidence, and a strict production-verifier bridge."""

import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform

from .duckdb_oracle import bounded_schema, enumerate_case, observe_row, validate_pair
from .production import ProductionUnavailable, load_production
from .row_evaluator import evaluate_encoded_row
from .sample_check import ROOT


FIXTURE_FIELDS = {
    "case_id", "schema_sql", "original_sql", "candidate_sql", "expected_status",
    "justification", "counterexample", "enumeration",
}
EXPECTED_STATUSES = {"EQUIVALENT", "DIFFERENT", "UNSAT_SCHEMA", "INVALID_INPUT", "INVALID_CANDIDATE"}
WITNESS_FIELDS = {"row", "original_truth", "candidate_truth", "original_rows", "candidate_rows"}
CONTROL_STATUSES = {"INVALID_INPUT", "INVALID_CANDIDATE"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _integer_map(value):
    return isinstance(value, dict) and all(isinstance(key, str) and type(item) is int for key, item in value.items())


def _projected_rows(value):
    return isinstance(value, list) and all(
        isinstance(row, list) and row and all(type(item) is int for item in row) for row in value
    )


def validate_fixture(case):
    require(isinstance(case, dict) and set(case) == FIXTURE_FIELDS, "Fixture must contain exactly the eight shared-contract fields")
    for key in FIXTURE_FIELDS - {"counterexample", "enumeration"}:
        require(isinstance(case[key], str) and case[key].strip(), f"{key} must be nonempty text")
    status = case["expected_status"]
    require(status in EXPECTED_STATUSES, f"Unsupported expected status: {status}")
    witness = case["counterexample"]
    if status == "DIFFERENT":
        require(isinstance(witness, dict) and set(witness) == WITNESS_FIELDS, "DIFFERENT requires a complete independent counterexample")
        require(_integer_map(witness["row"]) and bool(witness["row"]), "Counterexample row must be complete integer data")
        require(type(witness["original_truth"]) is bool and type(witness["candidate_truth"]) is bool, "Counterexample truths must be booleans")
        require(witness["original_truth"] != witness["candidate_truth"], "Counterexample truths must differ")
        require(_projected_rows(witness["original_rows"]) and _projected_rows(witness["candidate_rows"]), "Counterexample outputs must be integer row arrays")
    else:
        require(witness is None, "Only DIFFERENT fixtures have a counterexample")
    enumeration = case["enumeration"]
    if status in CONTROL_STATUSES:
        require(enumeration is None, "Malformed/unsupported SQL controls use null enumeration")
    elif enumeration is not None:
        require(isinstance(enumeration, dict) and set(enumeration) == {"domains", "fixed_values"}, "Enumeration needs domains and fixed_values")
        domains, fixed = enumeration["domains"], enumeration["fixed_values"]
        require(isinstance(domains, dict) and _integer_map(fixed), "Enumeration maps are malformed")
        require(not set(domains) & set(fixed), "Enumeration domains and fixed values overlap")
        for name, values in domains.items():
            require(isinstance(name, str) and isinstance(values, list) and values, "Each domain must be a nonempty integer array")
            require(all(type(value) is int for value in values) and len(set(values)) == len(values), "Domain values must be distinct integers")
    return case


def load_fixtures(path):
    cases, seen = [], set()
    for line_no, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = validate_fixture(json.loads(line))
            require(case["case_id"] not in seen, f"Duplicate case_id: {case['case_id']}")
        except (ValueError, TypeError) as error:
            raise ValueError(f"{path}:{line_no}: {error}") from error
        seen.add(case["case_id"])
        cases.append(case)
    require(bool(cases), "No verifier fixtures were supplied")
    return cases


def replay_witness(case, witness, schema_sql=None):
    """Check any supplied witness independently, without requiring the author's row."""
    original = candidate = None
    schema = case["schema_sql"] if schema_sql is None else schema_sql
    try:
        require(isinstance(witness, dict) and set(witness) == WITNESS_FIELDS, "Missing or malformed witness")
        require(_integer_map(witness["row"]), "Witness row must contain integers")
        require(type(witness["original_truth"]) is bool and type(witness["candidate_truth"]) is bool, "Witness truths must be booleans")
        require(witness["original_truth"] != witness["candidate_truth"], "Witness does not claim different truth values")
        require(_projected_rows(witness["original_rows"]) and _projected_rows(witness["candidate_rows"]), "Malformed witness projection rows")
        original = observe_row(schema, case["original_sql"], witness["row"])
        candidate = observe_row(schema, case["candidate_sql"], witness["row"])
        require(original["status"] == candidate["status"] == "OBSERVED", "Witness is not independently observable on a legal row")
        require(original["schema_holds"] is True and candidate["schema_holds"] is True, "Witness violates the schema")
        require(original["predicate_truth"] != candidate["predicate_truth"], "Witness predicates agree in DuckDB")
        for name, observation in (("original", original), ("candidate", candidate)):
            require(observation["predicate_truth"] == witness[f"{name}_truth"], f"{name} truth disagrees with DuckDB")
            require(Counter(map(tuple, observation["rows"])) == Counter(map(tuple, witness[f"{name}_rows"])), f"{name} projected multiset disagrees with DuckDB")
        require(Counter(map(tuple, original["rows"])) != Counter(map(tuple, candidate["rows"])), "Witness outputs do not distinguish the pair")
        return {"confirmed": True, "row": witness["row"], "schema_sql": schema, "original": original, "candidate": candidate, "diagnostic": None}
    except Exception as error:
        return {"confirmed": False, "witness": witness, "schema_sql": schema,
                "original": original, "candidate": candidate,
                "diagnostic": f"{type(error).__name__}: {error}"}


def offline_case(case):
    scope = validate_pair(case["schema_sql"], case["original_sql"], case["candidate_sql"])
    expected = case["expected_status"]
    record = {"case_id": case["case_id"], "expected_status": expected, "scope_status": scope, "counterexample_replay": None, "bounded": None, "errors": []}
    if expected in CONTROL_STATUSES:
        if scope != expected:
            record["errors"].append(f"Independent scope status {scope} differs from {expected}")
        return record
    if scope is not None:
        record["errors"].append(f"Supported fixture rejected by independent SQL scope check: {scope}")
        return record
    if expected == "DIFFERENT":
        record["counterexample_replay"] = replay_witness(case, case["counterexample"])
        if not record["counterexample_replay"]["confirmed"]:
            record["errors"].append("Author's independent counterexample failed replay")
    if case["enumeration"] is not None:
        bounded = enumerate_case(case)
        record["bounded"] = bounded
        if bounded.get("errors") or bounded["status"] not in {"EQUIVALENT", "DIFFERENT", "UNSAT_SCHEMA"}:
            record["errors"].append("Finite-domain DuckDB evaluation failed")
        elif expected == "EQUIVALENT" and (bounded["status"] != "EQUIVALENT" or bounded["legal_rows"] == 0):
            record["errors"].append("Equivalent fixture needs a legal row and matching bounded observations")
        elif expected == "UNSAT_SCHEMA" and bounded["status"] != "UNSAT_SCHEMA":
            record["errors"].append("Contradictory-schema fixture unexpectedly admits a legal row")
    elif expected == "EQUIVALENT":
        record["errors"].append("Equivalent fixture needs independent legal-row coverage")
    return record


def run_offline(cases):
    results = [offline_case(case) for case in cases]
    bounded = [result["bounded"] for result in results if result["bounded"] is not None]
    replays = [result["counterexample_replay"] for result in results if result["counterexample_replay"] is not None]
    summary = {
        "fixtures": len(cases), "expected_status_counts": dict(Counter(case["expected_status"] for case in cases)),
        "control_cases": sum(case["expected_status"] in CONTROL_STATUSES for case in cases),
        "independent_replays": len(replays), "confirmed_independent_replays": sum(replay["confirmed"] for replay in replays),
        "bounded_cases": len(bounded), "enumerated_rows": sum(item["total_rows"] for item in bounded),
        "legal_rows": sum(item["legal_rows"] for item in bounded),
        "distinguishing_rows": sum(item["distinguishing_rows"] for item in bounded),
        "failed_cases": sum(bool(result["errors"]) for result in results),
    }
    return {"summary": summary, "cases": results}


def production_not_run(reason, missing=()):
    return {
        "run_status": "NOT_RUN", "diagnostic": reason, "missing_interfaces": list(missing),
        "metrics": {key: None for key in (
            "false_equivalent", "false_different", "inconclusive_checks", "row_mismatches",
            "row_inconclusive", "row_errors", "sat_disagreement_models", "confirmed_replays", "confirmed_replay_rate",
        )},
    }


def check_result_errors(result):
    """Retain malformed production results as discrepancies, not successes."""
    fields = {"status", "original_nodes", "candidate_nodes", "diagnostic", "witness"}
    if not fields.issubset(result):
        return ["Missing CheckResult fields: " + ", ".join(sorted(fields - result.keys()))]
    errors = []
    if result["status"] not in {"EQUIVALENT", "DIFFERENT", "INVALID_INPUT", "INVALID_CANDIDATE", "UNSAT_SCHEMA", "INCONCLUSIVE", "REPLAY_ERROR"}:
        errors.append("Unknown CheckResult status")
    for name in ("original_nodes", "candidate_nodes"):
        value = result[name]
        if value is not None and (type(value) is not int or value <= 0):
            errors.append(f"{name} must be a positive integer or null")
    if result["status"] in ("EQUIVALENT", "DIFFERENT"):
        if result["diagnostic"] is not None:
            errors.append("Successful verdict diagnostic must be null")
        if any(type(result[name]) is not int or result[name] <= 0 for name in ("original_nodes", "candidate_nodes")):
            errors.append("Equivalent/different results need both positive node counts")
    elif not isinstance(result["diagnostic"], str) or not result["diagnostic"].strip():
        errors.append("Non-verdict outcome requires a diagnostic")
    if result["status"] != "DIFFERENT" and result["witness"] is not None:
        errors.append("Only DIFFERENT can carry a confirmed witness")
    return errors


def validation_result_errors(result):
    if not isinstance(result, dict) or not {"status", "original_nodes", "diagnostic"}.issubset(result):
        return ["ValidationResult needs status, original_nodes and diagnostic"]
    errors = []
    if result["status"] not in {"READY", "INVALID_INPUT", "UNSAT_SCHEMA", "INCONCLUSIVE"}:
        errors.append("Unknown ValidationResult status")
    nodes = result["original_nodes"]
    if nodes is not None and (type(nodes) is not int or nodes <= 0):
        errors.append("original_nodes must be a positive integer or null")
    if result["status"] == "READY":
        if type(nodes) is not int or nodes <= 0 or result["diagnostic"] is not None:
            errors.append("READY requires a positive original_nodes count and null diagnostic")
    elif not isinstance(result["diagnostic"], str) or not result["diagnostic"].strip():
        errors.append("Non-READY validation requires a diagnostic")
    return errors


def run_production(cases, offline, api, timeout_ms):
    """Consume real APIs in normal runs; tests may supply explicitly scripted APIs."""
    metrics = {key: 0 for key in (
        "full_checks", "bounded_checks", "false_equivalent", "false_different", "verdict_mismatches",
        "validation_mismatches", "validation_inconclusive", "inconclusive_checks", "row_checks", "row_mismatches", "row_inconclusive",
        "row_errors", "sat_disagreement_models", "confirmed_replays", "replay_errors", "infrastructure_errors",
    )}
    metrics["verdict_counts_by_stage"] = {stage: {} for stage in ("full_range", "bounded")}
    metrics["wrong_directions_by_stage"] = {stage: {"false_equivalent": 0, "false_different": 0} for stage in ("full_range", "bounded")}
    outcomes, discrepancies = [], []
    by_id = {item["case_id"]: item for item in offline["cases"]}

    def discrepancy(case, stage, expected, actual, **inputs):
        discrepancies.append({"case_id": case["case_id"], "stage": stage, "expected": expected, "actual": actual, "inputs": inputs})

    def check_pair(case, schema, expected, stage):
        request = {"schema_sql": schema, "original_sql": case["original_sql"], "candidate_sql": case["candidate_sql"], "timeout_ms": timeout_ms}
        metrics["full_checks" if stage == "full_range" else "bounded_checks"] += 1
        try:
            result = api.verify(**request)
            require(isinstance(result, dict) and isinstance(result.get("status"), str), "Malformed CheckResult")
        except Exception as error:
            metrics["infrastructure_errors"] += 1
            discrepancy(case, stage, expected, {"exception": str(error)}, **request)
            return {"error": str(error)}
        status = result["status"]
        stage_counts = metrics["verdict_counts_by_stage"][stage]
        stage_counts[status] = stage_counts.get(status, 0) + 1
        contract_errors = check_result_errors(result)
        if contract_errors:
            metrics["infrastructure_errors"] += 1
            discrepancy(case, f"{stage}_result_contract", "Complete shared CheckResult", {"errors": contract_errors, "result": result}, **request)
        if status == "INCONCLUSIVE":
            metrics["inconclusive_checks"] += 1
            discrepancy(case, stage, expected, result, **request)
        elif status != expected:
            metrics["verdict_mismatches"] += 1
            if expected == "DIFFERENT" and status == "EQUIVALENT":
                metrics["false_equivalent"] += 1
                metrics["wrong_directions_by_stage"][stage]["false_equivalent"] += 1
            if expected == "EQUIVALENT" and status == "DIFFERENT":
                metrics["false_different"] += 1
                metrics["wrong_directions_by_stage"][stage]["false_different"] += 1
            discrepancy(case, stage, expected, result, **request)
        if status in ("DIFFERENT", "REPLAY_ERROR"):
            metrics["sat_disagreement_models"] += 1
            if status == "REPLAY_ERROR":
                metrics["replay_errors"] += 1
            else:
                replay = replay_witness(case, result.get("witness"), schema)
                if replay["confirmed"]:
                    metrics["confirmed_replays"] += 1
                else:
                    discrepancy(case, f"{stage}_witness", "Legal distinguishing row confirmed in DuckDB", replay, **request)
        return result

    for case in cases:
        source_expected = "INVALID_INPUT" if case["expected_status"] == "INVALID_INPUT" else "UNSAT_SCHEMA" if case["expected_status"] == "UNSAT_SCHEMA" else "READY"
        record = {"case_id": case["case_id"], "validation": None, "full_range": None, "bounded": None}
        try:
            validation = api.validate_input(case["schema_sql"], case["original_sql"], timeout_ms=timeout_ms)
            record["validation"] = validation
            contract_errors = validation_result_errors(validation)
            if contract_errors:
                metrics["infrastructure_errors"] += 1
                discrepancy(case, "validation_result_contract", "Complete shared ValidationResult", {"errors": contract_errors, "result": validation}, schema_sql=case["schema_sql"], original_sql=case["original_sql"], timeout_ms=timeout_ms)
            if isinstance(validation, dict) and validation.get("status") == "INCONCLUSIVE":
                metrics["validation_inconclusive"] += 1
            if not isinstance(validation, dict) or validation.get("status") != source_expected:
                metrics["validation_mismatches"] += 1
                discrepancy(case, "validate_input", source_expected, validation, schema_sql=case["schema_sql"], original_sql=case["original_sql"], timeout_ms=timeout_ms)
        except Exception as error:
            metrics["infrastructure_errors"] += 1
            discrepancy(case, "validate_input", source_expected, str(error), schema_sql=case["schema_sql"], original_sql=case["original_sql"])
        record["full_range"] = check_pair(case, case["schema_sql"], case["expected_status"], "full_range")
        bounded = by_id[case["case_id"]]["bounded"]
        if bounded is not None:
            try:
                schema = bounded_schema(case["schema_sql"], case["enumeration"])
                record["bounded"] = {"schema_sql": schema, "expected_status": bounded["status"], "result": check_pair(case, schema, bounded["status"], "bounded")}
            except Exception as error:
                metrics["infrastructure_errors"] += 1
                discrepancy(case, "bounded_schema", "Exact-domain schema", str(error), schema_sql=case["schema_sql"], enumeration=case["enumeration"])
            for observation in bounded["observations"]:
                row = observation["row"]
                for source, query_field in (("original", "original_sql"), ("candidate", "candidate_sql")):
                    reference = observation[source]
                    try:
                        actual = evaluate_encoded_row(case["schema_sql"], case[query_field], row, timeout_ms=timeout_ms)
                        require(isinstance(actual, dict) and {"status", "schema_holds", "predicate_truth", "diagnostic"}.issubset(actual), "Malformed EncodedRowResult")
                        if actual["status"] == "EVALUATED":
                            require(type(actual["schema_holds"]) is bool and type(actual["predicate_truth"]) is bool and actual["diagnostic"] is None, "Nonconcrete or malformed encoded observation")
                    except Exception as error:
                        actual = {"status": "EVALUATION_ERROR", "schema_holds": None, "predicate_truth": None, "diagnostic": str(error)}
                    metrics["row_checks"] += 1
                    if actual["status"] == "INCONCLUSIVE":
                        metrics["row_inconclusive"] += 1
                        discrepancy(case, "encoded_row", reference, actual, schema_sql=case["schema_sql"], query_sql=case[query_field], row=row)
                    elif actual["status"] != "EVALUATED":
                        metrics["row_errors"] += 1
                        discrepancy(case, "encoded_row", reference, actual, schema_sql=case["schema_sql"], query_sql=case[query_field], row=row)
                    elif actual["schema_holds"] != reference["schema_holds"] or (reference["status"] == "OBSERVED" and actual["predicate_truth"] != reference["predicate_truth"]):
                        metrics["row_mismatches"] += 1
                        discrepancy(case, "encoded_row", reference, actual, schema_sql=case["schema_sql"], query_sql=case[query_field], row=row)
        outcomes.append(record)
    denominator = metrics["sat_disagreement_models"]
    metrics["confirmed_replay_rate"] = metrics["confirmed_replays"] / denominator if denominator else None
    return {"run_status": "COMPLETED", "metrics": metrics, "cases": outcomes, "discrepancies": discrepancies}


def dependency_versions():
    return {"python": platform.python_version(), **{name: importlib.metadata.version(name) for name in ("duckdb", "sqlglot", "z3-solver")}}


def compact_offline(report):
    """Keep case counts and replay inputs; successful row observations are reproducible."""
    compact = json.loads(json.dumps(report))
    for case in compact["cases"]:
        if case["bounded"] is not None:
            case["bounded"].pop("observations", None)
    return compact


def main(argv=None):
    parser = argparse.ArgumentParser(description="Independent Experiment A fixtures and production bridge")
    parser.add_argument("--fixtures", type=Path, default=ROOT / "evaluation/verifier_cases.jsonl")
    parser.add_argument("--fixtures-only", action="store_true", help="Run independent DuckDB checks without loading a verifier")
    parser.add_argument("--timeout-ms", type=int, default=5000)
    parser.add_argument("--output", type=Path, help="Write an English JSON report, excluding credentials")
    args = parser.parse_args(argv)
    if args.timeout_ms <= 0:
        parser.error("--timeout-ms must be a positive integer")
    report = {"mode": "fixtures_only" if args.fixtures_only else "production", "dependency_versions": dependency_versions(), "timeout_ms": args.timeout_ms}
    exit_code = 0
    try:
        cases = load_fixtures(args.fixtures)
        report["fixture_sha256"] = hashlib.sha256(args.fixtures.read_bytes()).hexdigest()
        report["fixtures_file"] = args.fixtures.resolve().relative_to(ROOT).as_posix() if args.fixtures.resolve().is_relative_to(ROOT) else args.fixtures.name
        api = None if args.fixtures_only else load_production()
        offline = run_offline(cases)
        report["offline"] = compact_offline(offline)
        print("OFFLINE SUMMARY " + json.dumps(offline["summary"], sort_keys=True))
        for item in offline["cases"]:
            bounded = item["bounded"]
            print(f"{'FAIL' if item['errors'] else 'PASS'} {item['case_id']}: full_range_author_label={item['expected_status']}; bounded={None if bounded is None else bounded['status']}; rows={0 if bounded is None else bounded['total_rows']}; legal={0 if bounded is None else bounded['legal_rows']}")
        if args.fixtures_only:
            report["production"] = production_not_run("Fixtures-only mode: no production API was called")
        elif offline["summary"]["failed_cases"]:
            report["production"] = production_not_run("Independent fixture checks must pass before production comparisons")
        else:
            report["production"] = run_production(cases, offline, api, args.timeout_ms)
            print("PRODUCTION METRICS " + json.dumps(report["production"]["metrics"], sort_keys=True))
            exit_code = int(bool(report["production"]["discrepancies"]))
        exit_code = max(exit_code, int(bool(offline["summary"]["failed_cases"])))
    except ProductionUnavailable as error:
        report["production"] = production_not_run(str(error), getattr(error, "missing", ()))
        print(f"BLOCKED: {error}")
        exit_code = 2
    except (ValueError, OSError) as error:
        report["error"] = str(error)
        report["production"] = production_not_run("Input or offline check failed")
        print(f"ERROR: {error}")
        exit_code = 1
    report["exit_code"] = exit_code
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return exit_code

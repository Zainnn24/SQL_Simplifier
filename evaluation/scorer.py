"""Score saved experiment logs against an explicit schedule, without SQL or APIs."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path


IDENTITY = {"record_type", "run_id", "task_id", "split", "repeat", "config_id"}
ATTEMPT = IDENTITY | {"attempt_no", "outcome", "raw_response", "candidate_sql", "check_result", "diagnostic", "feedback", "input_tokens", "output_tokens", "elapsed_ms"}
END = IDENTITY | {"preflight_result", "terminal_reason", "accepted", "selected_sql", "calls_used", "input_tokens", "output_tokens", "token_accounting_complete", "elapsed_ms", "diagnostic"}
CONFIG = {"config_id", "run_kind", "model_id", "sampling", "prompt_version", "output_parser", "solver_timeout_ms", "model_timeout_s", "max_model_calls", "transport_policy", "benchmark_version", "code_revision", "dependency_versions"}
BASE_TASK = {"task_id", "family", "split", "schema_sql", "original_sql", "reference_sql", "sample_rows", "expected_rows", "derivation", "source", "error_exposed"}
CHECK_STATUSES = {"EQUIVALENT", "DIFFERENT", "INVALID_INPUT", "INVALID_CANDIDATE", "UNSAT_SCHEMA", "INCONCLUSIVE", "REPLAY_ERROR"}
TERMINALS = {"ACCEPTED", "BUDGET_EXHAUSTED", "INVALID_INPUT", "UNSAT_SCHEMA", "INCONCLUSIVE", "REPLAY_ERROR", "API_ERROR", "INCOMPLETE"}


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _duration(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _rows(value):
    return isinstance(value, list) and all(isinstance(row, list) and bool(row) and all(type(item) is int for item in row) for row in value)


def _need(condition, message):
    if not condition:
        raise ValueError(message)


def _fields(value, required, name):
    _need(isinstance(value, dict), f"{name} must be an object")
    _need(required.issubset(value), f"{name} missing fields: {', '.join(sorted(required - value.keys()))}")


def _validation(value):
    _fields(value, {"status", "original_nodes", "diagnostic"}, "ValidationResult")
    _need(value["status"] in {"READY", "INVALID_INPUT", "UNSAT_SCHEMA", "INCONCLUSIVE"}, "Unknown preflight status")
    _need(value["original_nodes"] is None or _integer(value["original_nodes"], 1), "Preflight original_nodes must be positive integer or null")
    if value["status"] == "READY":
        _need(_integer(value["original_nodes"], 1) and value["diagnostic"] is None, "READY needs positive original_nodes and null diagnostic")
    else:
        _need(_text(value["diagnostic"]), "Non-READY preflight needs a diagnostic")


def _check(value):
    _fields(value, {"status", "original_nodes", "candidate_nodes", "diagnostic", "witness"}, "CheckResult")
    _need(value["status"] in CHECK_STATUSES, "Unknown verifier status")
    for name in ("original_nodes", "candidate_nodes"):
        _need(value[name] is None or _integer(value[name], 1), f"{name} must be positive integer or null")
    if value["status"] in {"EQUIVALENT", "DIFFERENT"}:
        _need(all(_integer(value[name], 1) for name in ("original_nodes", "candidate_nodes")), "Verified verdicts need both positive node counts")
        _need(value["diagnostic"] is None, "Verified verdict diagnostic must be null")
    else:
        _need(_text(value["diagnostic"]), "Non-verdict CheckResult needs a diagnostic")
    if value["status"] == "DIFFERENT":
        witness = value["witness"]
        _fields(witness, {"row", "original_truth", "candidate_truth", "original_rows", "candidate_rows"}, "Witness")
        _need(isinstance(witness["row"], dict) and bool(witness["row"]) and all(isinstance(key, str) and type(item) is int for key, item in witness["row"].items()), "Witness needs integer row data")
        _need(type(witness["original_truth"]) is bool and type(witness["candidate_truth"]) is bool and witness["original_truth"] != witness["candidate_truth"], "Witness needs different Boolean truth values")
        _need(_rows(witness["original_rows"]) and _rows(witness["candidate_rows"]), "Witness outputs must be integer row arrays")
        for name in ("original", "candidate"):
            _need(len(witness[f"{name}_rows"]) == int(witness[f"{name}_truth"]), "Single-row witness outputs must agree with its predicate truth values")
        _need(Counter(map(tuple, witness["original_rows"])) != Counter(map(tuple, witness["candidate_rows"])), "DIFFERENT witness projected multisets must differ")
    else:
        _need(value["witness"] is None, "Only DIFFERENT can carry a witness")


def _identity(record):
    _fields(record, IDENTITY, "Log identity")
    _need(all(_text(record[name]) for name in ("record_type", "run_id", "task_id", "split", "config_id")), "Identity strings must be nonempty")
    _need(_integer(record["repeat"], 1), "repeat must be a positive integer")
    _need(record["record_type"] in {"attempt", "run_end"}, "Unknown record_type")
    _need(record["split"] in {"dev", "test"}, "Unknown split")


def _record(record):
    _identity(record)
    _fields(record, ATTEMPT if record["record_type"] == "attempt" else END, record["record_type"])
    for key in ("input_tokens", "output_tokens"):
        _need(record[key] is None or _integer(record[key]), f"{key} must be nonnegative integer or null")
    _need(_duration(record["elapsed_ms"]), "elapsed_ms must be a finite nonnegative number")
    if record["record_type"] == "attempt":
        _need(_integer(record["attempt_no"], 1) and record["attempt_no"] <= 4, "attempt_no must be 1 through 4")
        _need(record["outcome"] in {"CHECKED", "OUTPUT_ERROR", "API_ERROR", "INCOMPLETE"}, "Unknown pipeline outcome")
        for name in ("raw_response", "candidate_sql", "feedback"):
            _need(record[name] is None or isinstance(record[name], str), f"{name} must be string or null")
        _need(record["feedback"] is None if record["attempt_no"] == 1 else _text(record["feedback"]), "Feedback must be null on attempt 1 and supplied on later attempts")
        if record["outcome"] == "CHECKED":
            _need(isinstance(record["raw_response"], str) and _text(record["candidate_sql"]), "CHECKED needs raw response and exact candidate SQL")
            _need(record["diagnostic"] is None, "CHECKED uses the nested verifier diagnostic")
            _check(record["check_result"])
        else:
            _need(record["check_result"] is None and _text(record["diagnostic"]), "Pipeline failures need a diagnostic and null check_result")
            if record["outcome"] == "OUTPUT_ERROR":
                _need(record["candidate_sql"] is None, "OUTPUT_ERROR cannot claim an extracted candidate")
    else:
        _validation(record["preflight_result"])
        _need(record["terminal_reason"] in TERMINALS, "Unknown terminal_reason")
        _need(type(record["accepted"]) is bool and type(record["token_accounting_complete"]) is bool, "Run-end flags must be booleans")
        _need(record["selected_sql"] is None or isinstance(record["selected_sql"], str), "selected_sql must be string or null")
        _need(record["diagnostic"] is None or isinstance(record["diagnostic"], str), "Run-end diagnostic must be string or null")
        _need(_integer(record["calls_used"]) and record["calls_used"] <= 4, "calls_used must be 0 through 4")


def _config(config):
    _fields(config, CONFIG, "RunConfig")
    string_fields = CONFIG - {"sampling", "dependency_versions", "solver_timeout_ms", "model_timeout_s", "max_model_calls"}
    _need(all(_text(config[key]) for key in string_fields), "RunConfig string fields must be nonempty")
    _need(config["run_kind"] in {"scripted", "model"}, "run_kind must be scripted or model")
    _need(isinstance(config["sampling"], dict), "sampling must be an object")
    _need(all(_integer(config[key], 1) for key in ("solver_timeout_ms", "model_timeout_s")), "Timeouts must be positive integers")
    _need(type(config["max_model_calls"]) is int and config["max_model_calls"] == 4, "max_model_calls must be 4")
    _need(config["transport_policy"] == "no_automatic_retry", "Unsupported transport policy")
    versions = config["dependency_versions"]
    _need(isinstance(versions, dict) and {"python", "sqlglot", "z3-solver", "duckdb"}.issubset(versions), "dependency_versions needs python, sqlglot, z3-solver and duckdb")
    _need(all(_text(key) and _text(value) for key, value in versions.items()), "Dependency versions must be nonempty strings")
    if config["run_kind"] == "model":
        values = [config["code_revision"], config["model_id"], config["benchmark_version"], config["prompt_version"], config["output_parser"], *versions.values()]
        _need(not any(value.upper().startswith(("RECORD_", "PLACEHOLDER", "TODO", "TBD")) for value in values), "Model measurements cannot use scripted version placeholders")


def _manifest_record(task):
    _fields(task, BASE_TASK, "Benchmark task")
    _need(all(_text(task[key]) for key in BASE_TASK - {"sample_rows", "expected_rows"}), "Task metadata and SQL fields must be nonempty strings")
    _need(task["split"] in {"dev", "test"}, "Task split must be dev or test")
    _need(isinstance(task["sample_rows"], list) and bool(task["sample_rows"]), "sample_rows must be a nonempty array")
    _need(all(isinstance(row, dict) and bool(row) and all(isinstance(key, str) and type(value) is int for key, value in row.items()) for row in task["sample_rows"]), "sample_rows must contain complete integer row objects")
    _need(_rows(task["expected_rows"]), "expected_rows must contain projected integer rows")


def _json(text):
    def reject(value):
        raise ValueError(f"Nonstandard JSON constant: {value}")
    return json.loads(text, parse_constant=reject)


class _Issues:
    def __init__(self):
        self.items = []
        self.bad_runs = set()

    def add(self, code, message, *, record=None, source=None, **identity):
        item = {"code": code, "message": message}
        if isinstance(record, dict):
            item.update({key: record[key] for key in ("run_id", "task_id", "repeat", "config_id") if key in record and type(record[key]) in (str, int)})
        item.update(identity)
        if source:
            item.update(source)
        self.items.append(item)
        if _text(item.get("run_id")):
            self.bad_runs.add(item["run_id"])


def _read(path, label, issues, sources, *, kind):
    try:
        data = Path(path).read_bytes()
        sources.append({"file": label, "sha256": hashlib.sha256(data).hexdigest()})
        return data.decode("utf-8-sig")
    except (OSError, UnicodeError) as error:
        issues.add(f"{kind}_READ_ERROR", str(error), source={"file": label})
        return None


def _lines(text, label, issues, *, kind):
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        source = {"file": label, "line": number}
        try:
            record = _json(line)
        except (ValueError, TypeError) as error:
            issues.add(f"{kind}_INVALID_JSON", str(error), source=source)
            continue
        yield record, source


def _stop(attempt):
    outcome = attempt["outcome"]
    if outcome in {"API_ERROR", "INCOMPLETE"}:
        return outcome
    if outcome == "OUTPUT_ERROR":
        return None
    check = attempt["check_result"]
    status = check["status"]
    if status == "EQUIVALENT":
        return "ACCEPTED" if check["candidate_nodes"] < check["original_nodes"] else None
    return None if status in {"DIFFERENT", "INVALID_CANDIDATE"} else status


def _rate(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator, "rate": numerator / denominator if denominator else None}


def _run(run_id, entries, task, config, issues):
    identity = entries[0][0]
    same_identity = lambda record: all(record.get(key) == identity.get(key) for key in ("task_id", "split", "repeat", "config_id"))
    attempts = [(record, source, valid) for record, source, valid in entries if record.get("record_type") == "attempt"]
    ends = [(record, source, valid) for record, source, valid in entries if record.get("record_type") == "run_end"]
    if any(not same_identity(record) for record, _, _ in entries):
        issues.add("RUN_IDENTITY_CONFLICT", "run_id is reused with different task/config/repeat/split identity", record=identity, source=entries[0][1])
    numbered = defaultdict(list)
    for entry in attempts:
        if _integer(entry[0].get("attempt_no"), 1):
            numbered[entry[0]["attempt_no"]].append(entry)
    for number, values in sorted(numbered.items()):
        if len(values) > 1:
            for record, source, _ in values:
                issues.add("DUPLICATE_ATTEMPT", f"Duplicate attempt_no {number}", record=record, source=source)
    numbers = sorted(numbered)
    if numbers != list(range(1, len(attempts) + 1)) or len(attempts) > 4:
        issues.add("ATTEMPT_SEQUENCE", "Dispatched attempts must be unique consecutive numbers 1 through 4", record=identity, source=entries[0][1])
    if len(ends) != 1:
        issues.add("MISSING_RUN_END" if not ends else "DUPLICATE_RUN_END", f"Expected exactly one run_end; found {len(ends)}", record=identity, source=ends[0][1] if ends else entries[0][1])
    end = ends[0][0] if len(ends) == 1 else None
    end_valid = len(ends) == 1 and ends[0][2]
    ordered = [values[0][0] for _, values in sorted(numbered.items())]
    structurally_valid = all(valid for _, _, valid in attempts) and len(ordered) == len(attempts) and numbers == list(range(1, len(attempts) + 1))
    for attempt, source, attempt_valid in attempts:
        if attempt_valid and attempt["outcome"] == "CHECKED" and attempt["candidate_sql"] == task.get("original_sql"):
            check = attempt["check_result"]
            if check["status"] == "DIFFERENT":
                issues.add("COPY_VERDICT_CONTRADICTION", "Byte-identical original and candidate SQL cannot have a DIFFERENT verdict", record=attempt, source=source, attempt_no=attempt["attempt_no"])
            if check["original_nodes"] is not None and check["candidate_nodes"] is not None and check["candidate_nodes"] != check["original_nodes"]:
                issues.add("COPY_NODE_MISMATCH", "A byte-identical copied original must retain its node count", record=attempt, source=source, attempt_no=attempt["attempt_no"])
    if end_valid:
        record, source, _ = ends[0]

        def problem(code, message):
            issues.add(code, message, record=record, source=source)

        if end["calls_used"] != len(attempts):
            problem("CALLS_MISMATCH", "calls_used differs from dispatched attempt-record count")
        try:
            attempt_elapsed = math.fsum(attempt["elapsed_ms"] for attempt, _, _ in attempts if _duration(attempt.get("elapsed_ms")))
            if end["elapsed_ms"] < attempt_elapsed and not math.isclose(end["elapsed_ms"], attempt_elapsed, rel_tol=1e-12, abs_tol=1e-6):
                problem("ELAPSED_TOTAL", "Total run elapsed_ms cannot be smaller than its sequential attempt elapsed_ms sum")
        except OverflowError:
            problem("ELAPSED_TOTAL", "Attempt elapsed_ms sum exceeds finite accounting range")
        preflight = end["preflight_result"]
        if preflight["status"] != "READY":
            if attempts or end["calls_used"] != 0 or end["terminal_reason"] != preflight["status"]:
                problem("PREFLIGHT_POLICY", "Non-READY preflight must stop with its status and zero model calls")
        elif structurally_valid:
            for attempt in ordered:
                if attempt["outcome"] == "CHECKED":
                    check = attempt["check_result"]
                    if check["original_nodes"] is not None and check["original_nodes"] != preflight["original_nodes"]:
                        problem("SOURCE_NODE_MISMATCH", f"Attempt {attempt['attempt_no']} source node count differs from READY")
            stops = [(index, _stop(attempt)) for index, attempt in enumerate(ordered) if _stop(attempt) is not None]
            if stops:
                index, terminal = stops[0]
                if index != len(ordered) - 1:
                    problem("AFTER_TERMINAL", "Another request was logged after a mandatory accept/stop result")
                if end["terminal_reason"] != terminal:
                    problem("TERMINAL_POLICY", f"Expected terminal reason {terminal} from the last decisive attempt")
            elif len(ordered) == 4:
                if end["terminal_reason"] != "BUDGET_EXHAUSTED":
                    problem("TERMINAL_POLICY", "Four retryable outcomes must end BUDGET_EXHAUSTED")
            elif end["terminal_reason"] not in ({"API_ERROR", "INCOMPLETE"} if not ordered else {"INCOMPLETE"}):
                problem("TERMINAL_POLICY", "Retryable outcomes below the cap need a recorded interruption, not a successful terminal claim")
        reason = end["terminal_reason"]
        if end["accepted"] != (reason == "ACCEPTED"):
            problem("ACCEPTED_FLAG", "accepted must be true exactly for ACCEPTED")
        if reason == "ACCEPTED":
            last = ordered[-1] if ordered else None
            if not last or not structurally_valid or last["outcome"] != "CHECKED" or _stop(last) != "ACCEPTED" or end["selected_sql"] != last["candidate_sql"]:
                problem("ACCEPTED_SELECTION", "ACCEPTED needs the last checked, strictly smaller equivalent candidate and its exact SQL")
        elif reason == "BUDGET_EXHAUSTED":
            if len(attempts) != 4 or end["selected_sql"] != task.get("original_sql"):
                problem("FALLBACK_POLICY", "Budget exhaustion needs four calls and the exact original SQL fallback")
        elif end["selected_sql"] is not None:
            problem("FAILURE_SELECTION", "Other terminal outcomes must have null selected_sql")
        complete_usage = all(_integer(attempt.get(key)) for attempt, _, _ in attempts for key in ("input_tokens", "output_tokens"))
        if end["token_accounting_complete"] != complete_usage:
            problem("TOKEN_COMPLETENESS", "token_accounting_complete disagrees with dispatched request usage")
        for key in ("input_tokens", "output_tokens"):
            known = [attempt[key] for attempt, _, _ in attempts if _integer(attempt.get(key))]
            expected = sum(known) if known or not attempts else None
            if end[key] != expected:
                problem("TOKEN_TOTAL", f"run_end {key} differs from the sum of known attempt usage")
    valid = run_id not in issues.bad_runs
    first_entries = numbered.get(1, [])
    first = first_entries[0][0] if len(first_entries) == 1 and first_entries[0][2] else None
    first_usable = first is not None and all(same_identity(record) for record, _, _ in entries) and bool(task) and bool(config)
    bad_identity = {"UNKNOWN_TASK", "INVALID_MANIFEST_RUN", "UNKNOWN_CONFIG", "INVALID_CONFIG_RUN", "RUN_KIND_MISMATCH", "TASK_SPLIT_MISMATCH", "UNSCHEDULED_SLOT", "SLOT_COLLISION", "RUN_IDENTITY_CONFLICT", "SOURCE_NODE_MISMATCH", "COPY_NODE_MISMATCH", "COPY_VERDICT_CONTRADICTION", "PREFLIGHT_POLICY"}
    first_usable = first_usable and not any(item.get("run_id") == run_id and item["code"] in bad_identity for item in issues.items)
    first_check = first["check_result"] if first_usable and first["outcome"] == "CHECKED" else None
    accepted = bool(valid and end_valid and end["terminal_reason"] == "ACCEPTED")
    reduction = None
    if accepted:
        check = ordered[-1]["check_result"]
        removed = check["original_nodes"] - check["candidate_nodes"]
        reduction = {"original_nodes": check["original_nodes"], "accepted_nodes": check["candidate_nodes"], "nodes_removed": removed, "percent_reduction": 100 * removed / check["original_nodes"]}
    known_input = sum(record["input_tokens"] for record, _, _ in attempts if _integer(record.get("input_tokens")))
    known_output = sum(record["output_tokens"] for record, _, _ in attempts if _integer(record.get("output_tokens")))
    input_unknown = sum(not _integer(record.get("input_tokens")) for record, _, _ in attempts)
    output_unknown = sum(not _integer(record.get("output_tokens")) for record, _, _ in attempts)
    terminal_counts = Counter(record.get("terminal_reason", "MALFORMED") for record, _, _ in ends if isinstance(record.get("terminal_reason", "MALFORMED"), str))
    outcomes = Counter()
    for attempt, _, _ in attempts:
        label = attempt.get("outcome", "MALFORMED")
        if label == "CHECKED" and isinstance(attempt.get("check_result"), dict):
            label = attempt["check_result"].get("status", "MALFORMED_CHECK_RESULT")
        if isinstance(label, str):
            outcomes[label] += 1
    return {
        "run_id": run_id, "task_id": identity["task_id"], "split": identity["split"], "repeat": identity["repeat"], "config_id": identity["config_id"],
        "run_kind": config.get("run_kind", "unknown"), "family": task.get("family"), "category": task.get("family", "")[:1] or None,
        "integrity_valid": valid, "first_equivalence": bool(first_check and first_check["status"] == "EQUIVALENT"),
        "first_simplification": bool(first_check and first_check["status"] == "EQUIVALENT" and first_check["candidate_nodes"] < first_check["original_nodes"]),
        "first_different": bool(first_check and first_check["status"] == "DIFFERENT"), "final_simplification": accepted, "reduction": reduction,
        "terminal_reason": end.get("terminal_reason") if end else None, "outcomes": dict(sorted(outcomes.items())), "terminal_counts": dict(sorted(terminal_counts.items())),
        "resources": {"calls": len(attempts), "calls_accounting_complete": bool(valid and end_valid), "declared_calls_used": end.get("calls_used") if end else None,
                      "elapsed_ms": end["elapsed_ms"] if end is not None and _duration(end.get("elapsed_ms")) else None, "input_tokens_known": known_input, "output_tokens_known": known_output,
                      "input_tokens_unknown_attempts": input_unknown, "output_tokens_unknown_attempts": output_unknown,
                      "token_accounting_complete": bool(valid and end_valid and not input_unknown and not output_unknown)},
        "sources": [{**source, "record_type": record.get("record_type"), "attempt_no": record.get("attempt_no")} for record, source, _ in entries],
    }


def _aggregate(config_id, config, repeat, tasks, runs, missing):
    denominator = len(tasks)
    reductions = [run["reduction"] for run in runs if run["reduction"] is not None]
    resource = [run["resources"] for run in runs]
    outcome = Counter()
    terminal = Counter()
    for run in runs:
        outcome.update(run["outcomes"])
        terminal.update(run["terminal_counts"])
    return {
        "run_kind": config["run_kind"], "config_id": config_id, "repeat": repeat, "scheduled_runs": denominator,
        "observed_runs": len(runs), "missing_runs": missing, "valid_runs": sum(run["integrity_valid"] for run in runs),
        "first_equivalence": _rate(sum(run["first_equivalence"] for run in runs), denominator),
        "first_simplification": _rate(sum(run["first_simplification"] for run in runs), denominator),
        "final_simplification": _rate(sum(run["final_simplification"] for run in runs), denominator),
        "recovery": _rate(sum(run["first_different"] and run["final_simplification"] for run in runs), sum(run["first_different"] for run in runs)),
        "reductions": {"accepted_runs": len(reductions), "nodes_removed_total": sum(item["nodes_removed"] for item in reductions),
                       "mean_nodes_removed": sum(item["nodes_removed"] for item in reductions) / len(reductions) if reductions else None,
                       "mean_percent_reduction": sum(item["percent_reduction"] for item in reductions) / len(reductions) if reductions else None},
        "resources": {"calls": sum(item["calls"] for item in resource), "calls_accounting_complete": not missing and all(item["calls_accounting_complete"] for item in resource),
                      "elapsed_ms": sum(item["elapsed_ms"] for item in resource if item["elapsed_ms"] is not None),
                      "elapsed_unknown_runs": missing + sum(item["elapsed_ms"] is None for item in resource),
                      "input_tokens_known": sum(item["input_tokens_known"] for item in resource), "output_tokens_known": sum(item["output_tokens_known"] for item in resource),
                      "input_tokens_unknown_attempts": sum(item["input_tokens_unknown_attempts"] for item in resource), "output_tokens_unknown_attempts": sum(item["output_tokens_unknown_attempts"] for item in resource),
                      "token_accounting_complete": not missing and all(item["token_accounting_complete"] for item in resource)},
        "outcomes": {"attempts": dict(sorted(outcome.items())), "runs": dict(sorted(terminal.items()))},
    }


def score_logs(manifest: Path, logs: Path, *, config_paths: list[Path], split: str = "test", repeats: tuple[int, ...] = (1, 2, 3), expected_tasks_per_repeat: int | None = None) -> dict:
    """Score immutable JSONL logs against manifest x configs x planned repeats."""
    issues = _Issues()
    sources = {"manifest": [], "configs": [], "logs": []}
    tasks, configs, bad_configs, bad_tasks = {}, {}, set(), set()
    manifest, logs = Path(manifest), Path(logs)
    text = _read(manifest, manifest.name, issues, sources["manifest"], kind="MANIFEST")
    if text is not None:
        for task, source in _lines(text, manifest.name, issues, kind="MANIFEST"):
            try:
                _manifest_record(task)
                _need(task["task_id"] not in tasks, "Duplicate manifest task_id")
            except (ValueError, TypeError) as error:
                issues.add("INVALID_MANIFEST_TASK", str(error), record=task, source=source)
                if isinstance(task, dict) and _text(task.get("task_id")):
                    bad_tasks.add(task["task_id"])
                continue
            tasks[task["task_id"]] = task
    for path in config_paths:
        path = Path(path)
        text = _read(path, path.name, issues, sources["configs"], kind="CONFIG")
        if text is None:
            continue
        config = None
        try:
            config = _json(text)
            _config(config)
        except (ValueError, TypeError) as error:
            issues.add("INVALID_CONFIG", str(error), record=config, source={"file": path.name})
            if not isinstance(config, dict) or not _text(config.get("config_id")) or not _text(config.get("run_kind")) or config["run_kind"] not in {"scripted", "model"}:
                continue
            bad_configs.add(config["config_id"])
        config_id = config["config_id"]
        if config_id in configs:
            issues.add("DUPLICATE_CONFIG", "config_id must identify exactly one supplied immutable config", config_id=config_id, source={"file": path.name})
            bad_configs.add(config_id)
            continue
        configs[config_id] = config
        sources["configs"][-1].update({"config_id": config_id, "run_kind": config["run_kind"]})
    if not configs:
        issues.add("NO_CONFIGS", "At least one explicit valid-identity config is required")
    if split not in {"dev", "test"}:
        issues.add("INVALID_PLAN", "split must be dev or test")
    planned_repeats = sorted({repeat for repeat in repeats if _integer(repeat, 1)})
    if not planned_repeats or len(planned_repeats) != len(repeats):
        issues.add("INVALID_PLAN", "Planned repeats must be distinct positive integers")
    selected = {task_id: task for task_id, task in tasks.items() if task["split"] == split}
    if not selected:
        issues.add("EMPTY_SPLIT", "Manifest has no tasks for the selected split")
    if expected_tasks_per_repeat is not None and (not _integer(expected_tasks_per_repeat, 1) or len(selected) != expected_tasks_per_repeat):
        issues.add("TASK_COUNT_MISMATCH", f"Selected manifest has {len(selected)} tasks; expected {expected_tasks_per_repeat}")
    scheduled = {(config_id, repeat, task_id, split) for config_id in configs for repeat in planned_repeats for task_id in selected}
    files = sorted(logs.rglob("*.jsonl")) if logs.is_dir() else [logs]
    if not files:
        issues.add("NO_LOG_FILES", "No JSONL log files were found")
    entries = defaultdict(list)
    for path in files:
        label = path.relative_to(logs).as_posix() if logs.is_dir() else path.name
        text = _read(path, label, issues, sources["logs"], kind="LOG")
        if text is None:
            continue
        for record, source in _lines(text, label, issues, kind="LOG"):
            try:
                _identity(record)
            except (ValueError, TypeError) as error:
                issues.add("INVALID_IDENTITY", str(error), record=record, source=source)
                continue
            valid = True
            try:
                _record(record)
            except (ValueError, TypeError) as error:
                issues.add("INVALID_RECORD", str(error), record=record, source=source)
                valid = False
            if record["task_id"] not in tasks:
                issues.add("UNKNOWN_TASK", "Log task_id is absent from the manifest", record=record, source=source)
            elif record["task_id"] in bad_tasks:
                issues.add("INVALID_MANIFEST_RUN", "Run references a task with invalid or conflicting manifest records", record=record, source=source)
            elif record["split"] != tasks[record["task_id"]]["split"]:
                issues.add("TASK_SPLIT_MISMATCH", "Log split differs from the task manifest", record=record, source=source)
            if record["config_id"] not in configs:
                issues.add("UNKNOWN_CONFIG", "Log config_id is not among the explicit configs", record=record, source=source)
            elif record["config_id"] in bad_configs:
                issues.add("INVALID_CONFIG_RUN", "Run references a config that failed validation", record=record, source=source)
            if "run_kind" in record and record["config_id"] in configs and record["run_kind"] != configs[record["config_id"]]["run_kind"]:
                issues.add("RUN_KIND_MISMATCH", "Log run_kind differs from its immutable config", record=record, source=source)
            slot = (record["config_id"], record["repeat"], record["task_id"], record["split"])
            if slot not in scheduled:
                issues.add("UNSCHEDULED_SLOT", "Log identity is outside the manifest/config/repeat schedule", record=record, source=source)
            entries[record["run_id"]].append((record, source, valid))
    slot_runs = defaultdict(set)
    for run_id, values in entries.items():
        for record, _, _ in values:
            slot_runs[(record["config_id"], record["repeat"], record["task_id"], record["split"])].add(run_id)
    for slot, run_ids in sorted(slot_runs.items()):
        if len(run_ids) > 1:
            for run_id in sorted(run_ids):
                issues.add("SLOT_COLLISION", "More than one run_id occupies this scheduled task/config/repeat/split slot", run_id=run_id, task_id=slot[2], config_id=slot[0], repeat=slot[1], split=slot[3], source=entries[run_id][0][1])
    for config_id, repeat, task_id, planned_split in sorted(scheduled):
        if (config_id, repeat, task_id, planned_split) not in slot_runs:
            issues.add("MISSING_SLOT", "Scheduled run has no log records", task_id=task_id, config_id=config_id, repeat=repeat, split=planned_split)
    runs = [_run(run_id, values, tasks.get(values[0][0]["task_id"], {}), configs.get(values[0][0]["config_id"], {}), issues) for run_id, values in sorted(entries.items())]
    repetitions, family_groups, category_groups = [], [], []
    for config_id, config in sorted(configs.items()):
        for repeat in planned_repeats:
            group_runs = [run for run in runs if run["config_id"] == config_id and run["repeat"] == repeat and run["task_id"] in selected and run["split"] == split]
            missing = sum((config_id, repeat, task_id, split) not in slot_runs for task_id in selected)
            repetitions.append(_aggregate(config_id, config, repeat, selected, group_runs, missing))
            for key, destination in (("family", family_groups), ("category", category_groups)):
                labels = sorted({task["family"] if key == "family" else task["family"][:1] for task in selected.values()})
                for label in labels:
                    subset = {task_id: task for task_id, task in selected.items() if (task["family"] if key == "family" else task["family"][:1]) == label}
                    subset_runs = [run for run in group_runs if run["task_id"] in subset]
                    missing_subset = sum((config_id, repeat, task_id, split) not in slot_runs for task_id in subset)
                    destination.append({key: label, **_aggregate(config_id, config, repeat, subset, subset_runs, missing_subset)})
    return {"format_version": "evaluation-score-v1", "mode": "offline_log_scoring", "split": split, "planned_repeats": planned_repeats,
            "plan": {"manifest": manifest.as_posix(), "logs": logs.as_posix(), "config_paths": [Path(path).as_posix() for path in config_paths],
                     "split": split, "repeats": planned_repeats, "expected_tasks_per_repeat": expected_tasks_per_repeat},
            "integrity": {"complete": not issues.items, "issues": issues.items}, "sources": sources,
            "repetitions": repetitions, "family_groups": family_groups, "category_groups": category_groups, "runs": runs}


def summarize(report):
    lines = [f"Log integrity: {'COMPLETE' if report['integrity']['complete'] else 'INCOMPLETE'}; issues={len(report['integrity']['issues'])}.",
             "Scripted groups are development evidence, separate from measured model groups."]
    for group in report["repetitions"]:
        def metric(name):
            value = group[name]
            return f"{value['numerator']}/{value['denominator']}" + (" (N/A)" if value["rate"] is None else f" ({value['rate']:.3f})")
        lines.append(f"{group['run_kind']} {group['config_id']} repeat={group['repeat']}: scheduled={group['scheduled_runs']}; first-equivalent={metric('first_equivalence')}; first-smaller={metric('first_simplification')}; final-smaller={metric('final_simplification')}; recovery={metric('recovery')}.")
        resources = group["resources"]
        mean_percent = group["reductions"]["mean_percent_reduction"]
        percent_text = "N/A" if mean_percent is None else f"{mean_percent:.2f}%"
        lines.append(f"  accepted={group['reductions']['accepted_runs']}; nodes-removed={group['reductions']['nodes_removed_total']}; accepted-mean-percent-reduction={percent_text}; calls={resources['calls']}; elapsed_ms={resources['elapsed_ms']}; known-input-tokens={resources['input_tokens_known']}; known-output-tokens={resources['output_tokens_known']}; token-accounting-complete={str(resources['token_accounting_complete']).lower()}.")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Score immutable JSONL logs without model or verifier calls")
    parser.add_argument("--manifest", type=Path, required=True, help="Frozen benchmark JSONL file used to define the scheduled task population")
    parser.add_argument("--logs", type=Path, required=True, help="JSONL log file or directory; directories are searched recursively for *.jsonl")
    parser.add_argument("--config", type=Path, action="append", required=True, help="Frozen RunConfig JSON file; repeat --config for each explicitly scheduled configuration")
    parser.add_argument("--split", choices=("dev", "test"), default="test", help="Manifest split to schedule (default: test)")
    parser.add_argument("--repeats", type=int, nargs="+", default=[1, 2, 3], help="Fixed planned repetition numbers, including repetitions missing from the logs (default: 1 2 3)")
    parser.add_argument("--expected-tasks-per-repeat", type=int, help="Optional selected-task population check; use 80 for the official held-out test experiment")
    parser.add_argument("--output", type=Path, help="Write a deterministic JSON report; the destination must not overwrite an input")
    parser.add_argument("--summary", type=Path, help="Write a concise English scoring summary to a separate text file")
    args = parser.parse_args(argv)
    protected = {args.manifest.resolve(), *(path.resolve() for path in args.config)}
    protected.update(path.resolve() for path in (args.logs.rglob("*.jsonl") if args.logs.is_dir() else [args.logs]))
    destinations = [path.resolve() for path in (args.output, args.summary) if path is not None]
    if any(path in protected for path in destinations) or len(set(destinations)) != len(destinations):
        parser.error("Output destinations must be distinct and must not overwrite manifest, configs or input logs")
    if args.logs.is_dir() and any(path.suffix.lower() == ".jsonl" and path.is_relative_to(args.logs.resolve()) for path in destinations):
        parser.error("Output destinations must not create JSONL files inside the input log tree")
    report = score_logs(args.manifest, args.logs, config_paths=args.config, split=args.split, repeats=tuple(args.repeats), expected_tasks_per_repeat=args.expected_tasks_per_repeat)
    summary = summarize(report)
    print(summary, end="")
    for path, text in ((args.output, json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"), (args.summary, summary)):
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
    return 0 if report["integrity"]["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

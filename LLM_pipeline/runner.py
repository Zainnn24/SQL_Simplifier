import json
from time import perf_counter
from typing import TextIO

from .logs import write_record
from .prompt import build_prompt, extract_candidate


_STOP_STATUSES = {"INVALID_INPUT", "UNSAT_SCHEMA", "INCONCLUSIVE", "REPLAY_ERROR"}


def run_task(
    task: dict, config: dict, *, repeat: int, model, validate_input, verify, log: TextIO
) -> dict:
    started = perf_counter()
    identity = {
        "run_id": f"{config['config_id']}-{task['task_id']}-r{repeat}",
        "task_id": task["task_id"],
        "split": task["split"],
        "repeat": repeat,
        "config_id": config["config_id"],
    }
    schema_sql = task["schema_sql"]
    original_sql = task["original_sql"]
    timeout_ms = config["solver_timeout_ms"]
    preflight = validate_input(schema_sql, original_sql, timeout_ms=timeout_ms)
    calls_used = 0
    feedback = None
    selected_sql = None
    diagnostic = preflight["diagnostic"]
    input_counts = []
    output_counts = []
    terminal_reason = preflight["status"]

    if terminal_reason == "READY":
        prompt = build_prompt(schema_sql, original_sql)
        for calls_used in range(1, config["max_model_calls"] + 1):
            attempt_started = perf_counter()
            request = prompt if feedback is None else f"{prompt}\n\n{feedback}"
            try:
                response = model.generate(request)
            except Exception as error:
                terminal_reason = "API_ERROR"
                diagnostic = str(error) or type(error).__name__
                write_record(
                    log, identity, "attempt", attempt_no=calls_used, outcome="API_ERROR",
                    raw_response=None, candidate_sql=None, check_result=None,
                    diagnostic=diagnostic, feedback=feedback, input_tokens=None,
                    output_tokens=None, elapsed_ms=(perf_counter() - attempt_started) * 1000,
                )
                break

            candidate = extract_candidate(response.raw_response)
            check = None
            output_error = None
            if candidate is None:
                output_error = "Return one complete SELECT statement as SQL."
            else:
                check = verify(schema_sql, original_sql, candidate, timeout_ms=timeout_ms)

            if response.input_tokens is not None:
                input_counts.append(response.input_tokens)
            if response.output_tokens is not None:
                output_counts.append(response.output_tokens)
            write_record(
                log, identity, "attempt", attempt_no=calls_used,
                outcome="OUTPUT_ERROR" if check is None else "CHECKED",
                raw_response=response.raw_response, candidate_sql=candidate, check_result=check,
                diagnostic=output_error, feedback=feedback, input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                elapsed_ms=(perf_counter() - attempt_started) * 1000,
            )

            if check is None:
                feedback = output_error
                continue
            status = check["status"]
            if status in _STOP_STATUSES:
                terminal_reason = status
                diagnostic = check["diagnostic"]
                break
            if status == "EQUIVALENT" and check["candidate_nodes"] < check["original_nodes"]:
                terminal_reason = "ACCEPTED"
                selected_sql = candidate
                break
            feedback = _retry_feedback(candidate, check)
        else:
            terminal_reason = "BUDGET_EXHAUSTED"
            selected_sql = original_sql

    result = {
        "preflight_result": preflight,
        "terminal_reason": terminal_reason,
        "accepted": terminal_reason == "ACCEPTED",
        "selected_sql": selected_sql,
        "calls_used": calls_used,
        "input_tokens": sum(input_counts) if input_counts or not calls_used else None,
        "output_tokens": sum(output_counts) if output_counts or not calls_used else None,
        "token_accounting_complete": len(input_counts) == len(output_counts) == calls_used,
        "elapsed_ms": (perf_counter() - started) * 1000,
        "diagnostic": diagnostic,
    }
    write_record(log, identity, "run_end", **result)
    return result


def _retry_feedback(candidate: str, check: dict) -> str:
    if check["status"] == "DIFFERENT":
        witness = check["witness"]
        row = ", ".join(f"{column}={value}" for column, value in witness["row"].items())
        return (
            f"Your candidate {candidate} changes the result on a legal row with {row}. "
            f"Original truth={json.dumps(witness['original_truth'])}, "
            f"candidate truth={json.dumps(witness['candidate_truth'])}. "
            f"Original rows={witness['original_rows']}, candidate rows={witness['candidate_rows']}. "
            "Revise the query to preserve row selection and reduce predicate size."
        )
    if check["status"] == "EQUIVALENT":
        return (
            f"Your candidate has {check['candidate_nodes']} predicate nodes and the original "
            f"has {check['original_nodes']}. Return a strictly smaller equivalent query."
        )
    if check["status"] == "INVALID_CANDIDATE":
        return f"{check['diagnostic']} Return one complete SELECT using the allowed syntax."
    raise ValueError(f"Unexpected verifier status: {check['status']!r}")

"""Independent BIGINT row probes and hand-counted calibration requests.

Out-of-range values appear only in Python rows, never in generated SQL. The
calibration counts below are fixed examples from the shared contract; this
module does not translate SQL or count arbitrary query syntax trees.
"""

from .duckdb_oracle import BIGINT_MAX, BIGINT_MIN, observe_row


PROBE_SCHEMA = "CREATE TABLE t (row_id BIGINT NOT NULL, x BIGINT NOT NULL)"
PROBE_QUERY = "SELECT row_id FROM t WHERE x >= 0"
CALIBRATION_E2_SCHEMA = (
    "CREATE TABLE jobs (row_id BIGINT NOT NULL, start_day BIGINT NOT NULL, "
    "end_day BIGINT NOT NULL, CHECK (start_day <= end_day))"
)
CALIBRATION_E2_QUERY = "SELECT row_id FROM jobs WHERE start_day >= 10 AND end_day >= 10"
CALIBRATION_CASES = (
    {
        "case_id": "calibration-boundary-3-3", "schema_sql": PROBE_SCHEMA,
        "original_sql": PROBE_QUERY, "candidate_sql": "SELECT row_id FROM t WHERE x > 0",
        "expected_status": "DIFFERENT", "original_nodes": 3, "candidate_nodes": 3,
    },
    {
        "case_id": "calibration-e2-7-3", "schema_sql": CALIBRATION_E2_SCHEMA,
        "original_sql": CALIBRATION_E2_QUERY,
        "candidate_sql": "SELECT row_id FROM jobs WHERE start_day >= 10",
        "expected_status": "EQUIVALENT", "original_nodes": 7, "candidate_nodes": 3,
    },
    {
        "case_id": "calibration-e2-copy-7-7", "schema_sql": CALIBRATION_E2_SCHEMA,
        "original_sql": CALIBRATION_E2_QUERY, "candidate_sql": CALIBRATION_E2_QUERY,
        "expected_status": "EQUIVALENT", "original_nodes": 7, "candidate_nodes": 7,
    },
)


def independent_type_bound_probes():
    """Obtain eight expectations from actual DuckDB CREATE/INSERT operations."""
    probes, errors = [], []
    boundaries = (
        ("below_minimum", BIGINT_MIN - 1), ("minimum", BIGINT_MIN),
        ("maximum", BIGINT_MAX), ("above_maximum", BIGINT_MAX + 1),
    )
    for column in ("row_id", "x"):
        for boundary, value in boundaries:
            row = {"row_id": 0, "x": 0}
            row[column] = value
            expected = observe_row(PROBE_SCHEMA, PROBE_QUERY, row)
            probe = {
                "probe_id": f"type-bound-{column}-{boundary}", "column": column,
                "boundary": boundary, "value": value, "row": row,
                "schema_sql": PROBE_SCHEMA, "query_sql": PROBE_QUERY,
                "expected_schema_holds": expected["schema_holds"], "expected": expected,
            }
            probes.append(probe)
            if expected["status"] not in ("OBSERVED", "REJECTED_TYPE") or type(expected["schema_holds"]) is not bool:
                errors.append({"probe_id": probe["probe_id"], "expected": expected})
    return {"run_status": "OFFLINE_EXPECTATIONS", "probe_count": len(probes), "probes": probes, "errors": errors}


def run_type_bound_probes(expectations, evaluate_row, timeout_ms):
    """Compare production-encoded fixed rows with the independent insert results."""
    metrics = {name: 0 for name in (
        "type_bound_probes", "type_bound_mismatches", "type_bound_errors", "type_bound_inconclusive",
    )}
    probes, discrepancies = [], []
    for expected_probe in expectations["probes"]:
        probe = dict(expected_probe)
        reference = probe["expected"]
        metrics["type_bound_probes"] += 1
        request = {"schema_sql": probe["schema_sql"], "query_sql": probe["query_sql"], "row": probe["row"], "timeout_ms": timeout_ms}
        try:
            actual = evaluate_row(**request)
            if not isinstance(actual, dict) or not {"status", "schema_holds", "predicate_truth", "diagnostic"}.issubset(actual):
                raise ValueError("Malformed EncodedRowResult for type-bound probe")
            if actual["status"] == "EVALUATED" and (
                type(actual["schema_holds"]) is not bool or type(actual["predicate_truth"]) is not bool or actual["diagnostic"] is not None
            ):
                raise ValueError("Nonconcrete or malformed type-bound observation")
        except Exception as error:
            actual = {"status": "EVALUATION_ERROR", "schema_holds": None, "predicate_truth": None, "diagnostic": f"{type(error).__name__}: {error}"}
        probe["actual"] = actual
        matched = False
        if reference["status"] not in ("OBSERVED", "REJECTED_TYPE"):
            metrics["type_bound_errors"] += 1
        elif actual["status"] == "INCONCLUSIVE":
            metrics["type_bound_inconclusive"] += 1
        elif actual["status"] != "EVALUATED":
            metrics["type_bound_errors"] += 1
        else:
            matched = actual["schema_holds"] == reference["schema_holds"] and (
                reference["status"] != "OBSERVED" or actual["predicate_truth"] == reference["predicate_truth"]
            )
            if not matched:
                metrics["type_bound_mismatches"] += 1
        probe["matched"] = matched
        if not matched:
            discrepancies.append({
                "case_id": probe["probe_id"], "stage": "type_bound_probe", "expected": reference,
                "actual": actual, "inputs": {**request, "column": probe["column"], "value": probe["value"]},
            })
        probes.append(probe)
    return {"run_status": "COMPLETED", "metrics": metrics, "probes": probes, "discrepancies": discrepancies}

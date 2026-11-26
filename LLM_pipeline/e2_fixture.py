"""Scripted preflight and verifier results for the E2-01 development run."""

_SCHEMA_SQL = (
    "CREATE TABLE jobs (row_id BIGINT NOT NULL, start_day BIGINT NOT NULL, "
    "end_day BIGINT NOT NULL, CHECK (start_day <= end_day))"
)
_ORIGINAL_SQL = "SELECT row_id FROM jobs WHERE start_day >= 10 AND end_day >= 10"
_WRONG_SQL = "SELECT row_id FROM jobs WHERE end_day >= 10"
_SMALLER_SQL = "SELECT row_id FROM jobs WHERE start_day >= 10"


def dummy_validate_input(schema_sql: str, original_sql: str, *, timeout_ms: int) -> dict:
    if (schema_sql, original_sql) != (_SCHEMA_SQL, _ORIGINAL_SQL):
        raise ValueError("The scripted verifier expects the E2-01 schema and original query.")
    return {"status": "READY", "original_nodes": 7, "diagnostic": None}


def dummy_verify(
    schema_sql: str, original_sql: str, candidate_sql: str, *, timeout_ms: int
) -> dict:
    preflight = dummy_validate_input(schema_sql, original_sql, timeout_ms=timeout_ms)
    original_nodes = preflight["original_nodes"]
    if candidate_sql == _WRONG_SQL:
        return {
            "status": "DIFFERENT",
            "original_nodes": original_nodes,
            "candidate_nodes": 3,
            "diagnostic": None,
            "witness": {
                "row": {"row_id": 2, "start_day": 9, "end_day": 10},
                "original_truth": False,
                "candidate_truth": True,
                "original_rows": [],
                "candidate_rows": [[2]],
            },
        }
    if candidate_sql in {_SMALLER_SQL, _ORIGINAL_SQL}:
        return {
            "status": "EQUIVALENT",
            "original_nodes": original_nodes,
            "candidate_nodes": original_nodes if candidate_sql == _ORIGINAL_SQL else 3,
            "diagnostic": None,
            "witness": None,
        }
    raise ValueError(f"Unexpected SQL for the E2-01 verifier fixture: {candidate_sql!r}")

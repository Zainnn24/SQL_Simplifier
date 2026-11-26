"""Check finite DuckDB samples against author-provided expected multisets."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import sys

import duckdb


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TASKS = (ROOT / "evaluation/B3-01.json", ROOT / "evaluation/C1-01.json")
DEFAULT_SEEDS = ROOT / "data/SQL_Predicate_Family_Examples.sql"
Row = tuple[int, ...]


@dataclass(frozen=True)
class RowCheck:
    """One independent comparison, retaining row multiplicity and column order."""

    task_id: str
    source: str
    actual_rows: tuple[Row, ...] = ()
    missing: Counter[Row] = field(default_factory=Counter)
    unexpected: Counter[Row] = field(default_factory=Counter)
    error: str | None = None
    columns: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.error is None and not self.missing and not self.unexpected


def load_task(path: Path) -> dict:
    """Load one JSON task; full benchmark acceptance is outside this tool."""
    task = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(task, dict):
        raise ValueError("Task record must be a JSON object")
    return task


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _single_statement(connection, sql: str, statement_type: str) -> None:
    statements = connection.extract_statements(sql)
    if len(statements) != 1 or statements[0].type.name != statement_type:
        raise ValueError(f"Expected exactly one {statement_type} statement")


def _table_info(connection) -> tuple[str, tuple[str, ...]]:
    tables = connection.execute(
        "SELECT schema_name, table_name FROM duckdb_tables() WHERE NOT internal"
    ).fetchall()
    if len(tables) != 1:
        raise ValueError("Expected exactly one sample table")
    schema, table = tables[0]
    columns = tuple(
        row[0]
        for row in connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
            [schema, table],
        ).fetchall()
    )
    return f"{_quote_identifier(schema)}.{_quote_identifier(table)}", columns


def _validate_task(task: dict) -> None:
    for name in ("task_id", "schema_sql", "original_sql", "reference_sql"):
        if not isinstance(task.get(name), str) or not task[name].strip():
            raise ValueError(f"{name} must be a nonempty string")
    samples = task.get("sample_rows")
    if not isinstance(samples, list) or not samples:
        raise ValueError("sample_rows must be a nonempty array of row objects")
    for index, row in enumerate(samples):
        if not isinstance(row, dict) or not row:
            raise ValueError(f"sample_rows[{index}] must be a nonempty row object")
        if any(not isinstance(key, str) for key in row):
            raise ValueError(f"sample_rows[{index}] column names must be strings")
        if any(type(value) is not int for value in row.values()):
            raise ValueError(f"sample_rows[{index}] values must be integers, not booleans or nulls")
    expected = task.get("expected_rows")
    if not isinstance(expected, list):
        raise ValueError("expected_rows must be an array of projected integer rows")
    for index, row in enumerate(expected):
        if not isinstance(row, list) or not row or any(type(value) is not int for value in row):
            raise ValueError(f"expected_rows[{index}] must be a nonempty integer array")
    if expected and any(len(row) != len(expected[0]) for row in expected):
        raise ValueError("expected_rows must have one consistent projection width")


def _sample_values(samples: list[dict], columns: tuple[str, ...]) -> tuple[Row, ...]:
    values = []
    for index, row in enumerate(samples):
        missing = sorted(set(columns) - row.keys())
        unexpected = sorted(row.keys() - set(columns))
        if missing or unexpected:
            raise ValueError(
                f"sample_rows[{index}] column mismatch: missing={missing}, unexpected={unexpected}"
            )
        values.append(tuple(row[column] for column in columns))
    return tuple(values)


def _compare(task_id: str, source: str, actual, expected, columns=()) -> RowCheck:
    actual_rows = tuple(tuple(row) for row in actual)
    actual_counts = Counter(actual_rows)
    expected_counts = Counter(tuple(row) for row in expected)
    return RowCheck(
        task_id=task_id,
        source=source,
        actual_rows=actual_rows,
        missing=expected_counts - actual_counts,
        unexpected=actual_counts - expected_counts,
        columns=tuple(columns),
    )


def _seed_rows(seed_file: Path, family: str) -> tuple[tuple[str, ...], tuple[Row, ...]]:
    """Execute only the CREATE/INSERT statements in one labeled SQL seed block."""
    text = Path(seed_file).read_text(encoding="utf-8")
    headings = list(re.finditer(r"^--\s+([A-E][1-5])\.\s+.*$", text, flags=re.MULTILINE))
    matches = [index for index, heading in enumerate(headings) if heading[1] == family]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one SQL seed heading for family {family}")
    index = matches[0]
    start = headings[index].start()
    end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
    block = text[start:end]
    with duckdb.connect(":memory:") as connection:
        created = inserted = False
        for statement in connection.extract_statements(block):
            kind = statement.type.name
            if kind in ("CREATE", "INSERT"):
                connection.execute(statement.query)
                created = created or kind == "CREATE"
                inserted = inserted or kind == "INSERT"
            elif kind not in ("SELECT", "TRANSACTION"):
                raise ValueError(f"Unexpected {kind} statement in SQL seed {family}")
        if not created or not inserted:
            raise ValueError(f"SQL seed {family} needs CREATE and INSERT statements")
        table, columns = _table_info(connection)
        rows = tuple(connection.execute(f"SELECT * FROM {table}").fetchall())
    return columns, rows


def check_task(task: dict, *, seed_file: Path | None = None) -> list[RowCheck]:
    """Check each query against expected rows in a new in-memory task database.

    Optional seed checks compare every named input column, preserving duplicates.
    Query mismatches and execution errors are reported independently. Passing these
    checks does not prove equivalence on unobserved rows or accept a benchmark.
    """
    task_id = str(task.get("task_id", "<unknown>"))
    results = []
    try:
        _validate_task(task)
        with duckdb.connect(":memory:") as connection:
            _single_statement(connection, task["schema_sql"], "CREATE")
            connection.execute(task["schema_sql"])
            table, columns = _table_info(connection)
            samples = _sample_values(task["sample_rows"], columns)
            names = ", ".join(_quote_identifier(column) for column in columns)
            placeholders = ", ".join("?" for _ in columns)
            connection.executemany(
                f"INSERT INTO {table} ({names}) VALUES ({placeholders})", samples
            )

            if seed_file is not None:
                try:
                    family = task.get("family")
                    if not isinstance(family, str) or task_id != f"{family}-01":
                        raise ValueError("Seed consistency requires a family and its variant-01 task")
                    seed_columns, expected_samples = _seed_rows(seed_file, family)
                    actual_samples = _sample_values(task["sample_rows"], seed_columns)
                    results.append(_compare(
                        task_id, "seed_rows", actual_samples, expected_samples, seed_columns
                    ))
                except (OSError, ValueError, duckdb.Error) as error:
                    results.append(RowCheck(task_id, "seed_rows", error=str(error)))

            for source in ("original_sql", "reference_sql"):
                try:
                    _single_statement(connection, task[source], "SELECT")
                    cursor = connection.execute(task[source])
                    output_columns = tuple(item[0] for item in cursor.description)
                    actual = cursor.fetchall()
                    if task["expected_rows"] and len(output_columns) != len(task["expected_rows"][0]):
                        raise ValueError(
                            f"Projection width {len(output_columns)} differs from "
                            f"expected width {len(task['expected_rows'][0])}"
                        )
                    if any(type(value) is not int for row in actual for value in row):
                        raise ValueError("Query results must contain only non-null integers")
                    results.append(_compare(
                        task_id, source, actual, task["expected_rows"], output_columns
                    ))
                except (ValueError, duckdb.Error) as error:
                    results.append(RowCheck(task_id, source, error=str(error)))
    except (ValueError, duckdb.Error) as error:
        results.append(RowCheck(task_id, "input", error=str(error)))
    return results


def _count_details(counts: Counter[Row]) -> str:
    return json.dumps([
        {"row": list(row), "count": count} for row, count in sorted(counts.items())
    ])


def format_check(result: RowCheck) -> str:
    """Render deterministic diagnostics with the task, query and exact row counts."""
    prefix = f"{'PASS' if result.passed else 'FAIL'} {result.task_id} {result.source}:"
    if result.error is not None:
        return f"{prefix} error={result.error}"
    columns = json.dumps(result.columns)
    if result.passed:
        rows = json.dumps(sorted(result.actual_rows))
        return f"{prefix} columns={columns} rows={rows}"
    return (
        f"{prefix} columns={columns} missing={_count_details(result.missing)} "
        f"unexpected={_count_details(result.unexpected)}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Finite DuckDB sample checks for local task records")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("check-samples", help="Check samples, not full benchmark acceptance")
    command.add_argument("--tasks", nargs="+", type=Path, help="JSON task files; default: B3-01 and C1-01")
    command.add_argument(
        "--seed-file", type=Path,
        help="Also check variant-01 inputs against this SQL seed file (enabled for default tasks)",
    )
    args = parser.parse_args(argv)
    paths = args.tasks if args.tasks is not None else DEFAULT_TASKS
    seeds = args.seed_file
    if args.tasks is None and seeds is None:
        seeds = DEFAULT_SEEDS
    print("Sample checks only: no full equivalence proof or 100-task benchmark acceptance.")
    print(f"Python {sys.version.split()[0]}; DuckDB {duckdb.__version__}")
    print(f"Seed consistency: {seeds if seeds is not None else 'SKIPPED (custom tasks; not requested)'}")
    failed_tasks = failed_checks = total_checks = 0
    for path in paths:
        try:
            task = load_task(path)
            samples = task.get("sample_rows")
            sample_count = len(samples) if isinstance(samples, list) else "INVALID"
            print(f"TASK {task.get('task_id', '<unknown>')}: sample_rows={sample_count}")
            results = check_task(task, seed_file=seeds)
        except (OSError, ValueError) as error:
            results = [RowCheck(path.stem, "input", error=f"{path}: {error}")]
        for result in results:
            print(format_check(result))
        total_checks += len(results)
        failed_checks += sum(not result.passed for result in results)
        failed_tasks += any(not result.passed for result in results)
    print(
        f"Sample check summary: {len(paths)} task(s), {total_checks} check(s), "
        f"{failed_tasks} failed task(s), {failed_checks} failed check(s)."
    )
    return 1 if failed_checks else 0

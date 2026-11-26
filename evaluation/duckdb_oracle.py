"""Independent SQL scope checks and concrete DuckDB observations, without SMT.

Finite-domain verdicts describe only the enumerated rows. They are not full-width
equivalence proofs and do not consume a production or test SQL-to-Z3 translator.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import product
import math
import re

import duckdb
import sqlglot
from sqlglot import exp
from sqlglot.tokens import TokenType


BIGINT_MIN = -(2**63)
BIGINT_MAX = 2**63 - 1
MAX_ENUMERATION_ROWS = 10_000
HARD_ENUMERATION_LIMIT = 100_000


class ScopeError(ValueError):
    """Malformed or unsupported independent-oracle input."""


@dataclass(frozen=True)
class _Schema:
    expression: exp.Create
    table: str
    columns: tuple[str, ...]


def _require(condition, message):
    if not condition:
        raise ScopeError(message)


def _parse(sql):
    _require(isinstance(sql, str) and sql.strip(), "SQL must be a nonempty string")
    try:
        statements = [
            statement for statement in sqlglot.parse(sql, read="duckdb")
            if statement is not None and not isinstance(statement, exp.Semicolon)
        ]
    except (sqlglot.errors.SqlglotError, ValueError) as error:
        raise ScopeError(f"Cannot parse DuckDB SQL: {error}") from error
    _require(len(statements) == 1, "Expected exactly one SQL statement")
    return statements[0]


def _only_args(node, allowed):
    for name, value in node.args.items():
        _require(name in allowed or not value, f"Unsupported {type(node).__name__} option: {name}")


def _table_name(node):
    _require(isinstance(node, exp.Table) and isinstance(node.this, exp.Identifier), "Expected one named table")
    _only_args(node, {"this"})
    return node.name


def _column(node, schema):
    _require(isinstance(node, exp.Column) and isinstance(node.this, exp.Identifier), "Expected a declared column")
    _only_args(node, {"this", "table"})
    _require(not node.table or node.table.casefold() == schema.table.casefold(), "Unknown column qualifier")
    names = {name.casefold(): name for name in schema.columns}
    _require(node.name.casefold() in names, f"Unknown column: {node.name}")
    return names[node.name.casefold()]


def _term(node, schema):
    if isinstance(node, exp.Paren):
        return _term(node.this, schema)
    if isinstance(node, exp.Column):
        _column(node, schema)
        return
    sign = 1
    if isinstance(node, exp.Neg):
        sign, node = -1, node.this
    _require(
        isinstance(node, exp.Literal) and not node.is_string
        and re.fullmatch(r"\d+", str(node.this)) is not None,
        "Integer terms must be columns or signed integer literals",
    )
    value = sign * int(node.this)
    _require(BIGINT_MIN <= value <= BIGINT_MAX, "SQL integer literal is outside signed BIGINT")


def _predicate(node, schema):
    if isinstance(node, exp.Paren):
        _predicate(node.this, schema)
    elif isinstance(node, exp.Boolean):
        return
    elif type(node) in (exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE):
        _term(node.this, schema)
        _term(node.expression, schema)
    elif type(node) in (exp.And, exp.Or):
        _predicate(node.this, schema)
        _predicate(node.expression, schema)
    elif isinstance(node, exp.Not):
        _predicate(node.this, schema)
    else:
        raise ScopeError(f"Unsupported predicate expression: {type(node).__name__}")


def _schema_info(schema_sql):
    create = _parse(schema_sql)
    _require(isinstance(create, exp.Create) and create.args.get("kind") == "TABLE", "Expected CREATE TABLE")
    _only_args(create, {"this", "kind"})
    _require(isinstance(create.this, exp.Schema), "CREATE TABLE must explicitly declare columns")
    table = _table_name(create.this.this)
    columns, checks = [], []
    for item in create.this.expressions:
        if isinstance(item, exp.ColumnDef):
            _only_args(item, {"this", "kind", "constraints"})
            _require(isinstance(item.this, exp.Identifier), "Expected a named column")
            kind = item.args.get("kind")
            _require(isinstance(kind, exp.DataType) and kind.this.name == "BIGINT" and not kind.expressions,
                     "Every column must have signed BIGINT type")
            columns.append(item.name)
            not_null = 0
            for constraint in item.args.get("constraints") or []:
                constraint_kind = constraint.args.get("kind")
                if isinstance(constraint_kind, exp.NotNullColumnConstraint):
                    _require(not constraint_kind.args.get("allow_null"), "Nullable columns are unsupported")
                    not_null += 1
                elif isinstance(constraint_kind, exp.CheckColumnConstraint):
                    checks.append(constraint_kind.this)
                else:
                    raise ScopeError("Only NOT NULL and row-local CHECK constraints are supported")
            _require(not_null == 1, "Every column needs exactly one explicit NOT NULL")
        elif isinstance(item, exp.CheckColumnConstraint):
            checks.append(item.this)
        elif isinstance(item, exp.Constraint):
            _require(bool(item.expressions), "Empty named constraint")
            for constraint in item.expressions:
                _require(isinstance(constraint, exp.CheckColumnConstraint), "Only row-local CHECK constraints are supported")
                checks.append(constraint.this)
        else:
            raise ScopeError(f"Unsupported schema element: {type(item).__name__}")
    _require(bool(columns), "At least one column is required")
    _require(len({column.casefold() for column in columns}) == len(columns), "Duplicate column names")
    schema = _Schema(create, table, tuple(columns))
    for check in checks:
        _predicate(check, schema)
    return schema


def schema_columns(schema_sql) -> tuple[str, ...]:
    """Validate the supported DDL without testing logical satisfiability."""
    return _schema_info(schema_sql).columns


def _query_info(query_sql, schema):
    query = _parse(query_sql)
    _require(isinstance(query, exp.Select), "Expected one SELECT")
    _only_args(query, {"expressions", "from_", "where"})
    source = query.args.get("from_")
    _require(isinstance(source, exp.From), "SELECT needs exactly one FROM table")
    _only_args(source, {"this"})
    _require(_table_name(source.this).casefold() == schema.table.casefold(), "SELECT uses a different table")
    _require(bool(query.expressions), "SELECT needs a nonempty ordered column projection")
    projection = tuple(_column(column, schema) for column in query.expressions)
    where = query.args.get("where")
    _require(isinstance(where, exp.Where), "SELECT requires an explicit WHERE predicate")
    _predicate(where.this, schema)
    return projection


def validate_pair(schema_sql, original_sql, candidate_sql) -> str | None:
    """Classify independent scope failures separately for source and candidate."""
    try:
        schema = _schema_info(schema_sql)
        projection = _query_info(original_sql, schema)
    except ScopeError:
        return "INVALID_INPUT"
    try:
        candidate_projection = _query_info(candidate_sql, schema)
        _require(projection == candidate_projection, "Candidate changes the ordered projection")
    except ScopeError:
        return "INVALID_CANDIDATE"
    return None


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _predicate_text(query_sql):
    """Isolate the original SQL text rather than regenerating any expression."""
    tokens = sqlglot.Dialect.get_or_raise("duckdb").tokenize(query_sql)
    where = next(index for index, token in enumerate(tokens) if token.token_type == TokenType.WHERE)
    predicate = [token for token in tokens[where + 1:] if token.token_type != TokenType.SEMICOLON]
    return query_sql[predicate[0].start:predicate[-1].end + 1]


def _observation(status, *, schema_holds=None, predicate_truth=None, rows=None, diagnostic=None):
    return {"status": status, "schema_holds": schema_holds, "predicate_truth": predicate_truth,
            "rows": rows, "diagnostic": diagnostic}


def _error_status(error):
    infrastructure = tuple(
        getattr(duckdb, name) for name in (
            "IOException", "OutOfMemoryException", "InternalException", "FatalException",
            "ConnectionException", "InterruptException",
        ) if hasattr(duckdb, name)
    )
    return "INFRASTRUCTURE_ERROR" if isinstance(error, infrastructure) or not isinstance(error, duckdb.Error) else "SQL_ERROR"


def _type_rejection(error, row):
    if not any(value < BIGINT_MIN or value > BIGINT_MAX for value in row.values()):
        return False
    types = (duckdb.ConversionException, duckdb.OutOfRangeException, duckdb.InvalidInputException, OverflowError)
    message = str(error).casefold()
    return isinstance(error, types) and any(text in message for text in (
        "out of range", "can't be cast", "cannot cast", "could not convert", "failed to cast", "overflow", "int too large", "integer too large",
    ))


def observe_row(schema_sql, query_sql, row) -> dict:
    """Observe row legality by insertion, then the original SQL truth and output."""
    try:
        schema = _schema_info(schema_sql)
        _query_info(query_sql, schema)
        _require(isinstance(row, dict) and set(row) == set(schema.columns), "Row must contain exactly the declared columns")
        _require(all(type(value) is int for value in row.values()), "Row values must be integers, excluding bool and null")
    except ScopeError as error:
        return _observation("INVALID_INPUT", diagnostic=str(error))
    try:
        connection = duckdb.connect(":memory:")
    except Exception as error:
        return _observation("INFRASTRUCTURE_ERROR", diagnostic=f"connect: {error}")
    legal = None
    try:
        connection.execute(schema_sql)
        columns = ", ".join(_quote(column) for column in schema.columns)
        placeholders = ", ".join("?" for _ in schema.columns)
        try:
            connection.execute(
                f"INSERT INTO {_quote(schema.table)} ({columns}) VALUES ({placeholders})",
                [row[column] for column in schema.columns],
            )
        except Exception as error:
            if isinstance(error, duckdb.ConstraintException) and "check constraint" in str(error).casefold():
                return _observation("REJECTED_CHECK", schema_holds=False, diagnostic=str(error))
            if _type_rejection(error, row):
                return _observation("REJECTED_TYPE", schema_holds=False, diagnostic=str(error))
            return _observation(_error_status(error), diagnostic=f"insert: {error}")
        legal = True
        truth = connection.execute(
            f"SELECT ({_predicate_text(query_sql)}) FROM {_quote(schema.table)}"
        ).fetchone()[0]
        _require(type(truth) is bool, "A legal non-null row must yield a Boolean predicate truth value")
        rows = [list(values) for values in connection.execute(query_sql).fetchall()]
        _require(all(type(value) is int for values in rows for value in values), "Projected values must be non-null integers")
        return _observation("OBSERVED", schema_holds=True, predicate_truth=truth, rows=rows)
    except ScopeError as error:
        return _observation("SQL_ERROR", schema_holds=legal, diagnostic=str(error))
    except Exception as error:
        return _observation(_error_status(error), schema_holds=legal, diagnostic=str(error))
    finally:
        try:
            connection.close()
        except Exception as error:
            return _observation("INFRASTRUCTURE_ERROR", schema_holds=legal, diagnostic=f"close: {error}")


def _enumeration(schema, enumeration, *, max_rows=MAX_ENUMERATION_ROWS):
    _require(isinstance(enumeration, dict) and set(enumeration) == {"domains", "fixed_values"},
             "Enumeration requires domains and fixed_values")
    domains, fixed = enumeration["domains"], enumeration["fixed_values"]
    _require(isinstance(domains, dict) and isinstance(fixed, dict), "Enumeration maps must be objects")
    _require(not set(domains).intersection(fixed), "Enumerated and fixed columns must be disjoint")
    _require(set(domains).union(fixed) == set(schema.columns), "Enumeration must cover exactly every declared column")
    for column, values in domains.items():
        _require(isinstance(values, list) and bool(values) and all(type(value) is int for value in values),
                 f"Domain {column} needs a nonempty integer list")
        _require(len(values) == len(set(values)), f"Domain {column} contains duplicate values")
    _require(all(type(value) is int for value in fixed.values()), "Fixed values must be integers, excluding bool and null")
    _require(type(max_rows) is int and 0 < max_rows <= HARD_ENUMERATION_LIMIT, "Invalid enumeration row limit")
    count = math.prod(len(values) for values in domains.values())
    _require(count <= max_rows, f"Enumeration has {count} rows, exceeding limit {max_rows}")
    return domains, fixed, count


def _enumeration_result():
    return {"status": "INVALID_INPUT", "total_rows": 0, "legal_rows": 0, "distinguishing_rows": 0,
            "distinguishing_examples": [], "observations": [], "errors": [], "original_rows": [], "candidate_rows": []}


def enumerate_case(case) -> dict:
    """Exhaust the explicitly recorded Cartesian domain using DuckDB alone."""
    result = _enumeration_result()
    try:
        _require(isinstance(case, dict), "Case must be an object")
        status = validate_pair(case.get("schema_sql"), case.get("original_sql"), case.get("candidate_sql"))
        if status:
            result["status"] = status
            result["errors"].append({"status": status, "diagnostic": "Independent SQL scope validation failed"})
            return result
        schema = _schema_info(case["schema_sql"])
        domains, fixed, count = _enumeration(schema, case.get("enumeration"))
    except ScopeError as error:
        result["errors"].append({"status": "INVALID_INPUT", "diagnostic": str(error)})
        return result
    result["total_rows"] = count
    varying = tuple(column for column in schema.columns if column in domains)
    rejected = {"REJECTED_CHECK", "REJECTED_TYPE"}
    for assignment in product(*(domains[column] for column in varying)):
        values = dict(fixed)
        values.update(zip(varying, assignment))
        row = {column: values[column] for column in schema.columns}
        original = observe_row(case["schema_sql"], case["original_sql"], row)
        candidate = observe_row(case["schema_sql"], case["candidate_sql"], row)
        result["observations"].append({"row": row, "original": original, "candidate": candidate})
        statuses = (original["status"], candidate["status"])
        if all(status == "OBSERVED" for status in statuses):
            result["legal_rows"] += 1
            result["original_rows"].extend(original["rows"])
            result["candidate_rows"].extend(candidate["rows"])
            if original["predicate_truth"] != candidate["predicate_truth"] or Counter(map(tuple, original["rows"])) != Counter(map(tuple, candidate["rows"])):
                result["distinguishing_rows"] += 1
                result["distinguishing_examples"].append(row)
        elif statuses[0] in rejected and statuses[1] == statuses[0]:
            continue
        else:
            errors = [
                {"row": row, "source": source, "status": observation["status"], "diagnostic": observation["diagnostic"]}
                for source, observation in (("original", original), ("candidate", candidate))
                if observation["status"] not in rejected | {"OBSERVED"}
            ]
            result["errors"].extend(errors or [{"row": row, "status": "SQL_ERROR", "diagnostic": "Inconsistent row legality under identical DDL"}])
    if result["errors"]:
        result["status"] = "INFRASTRUCTURE_ERROR" if any(error["status"] == "INFRASTRUCTURE_ERROR" for error in result["errors"]) else "SQL_ERROR"
    else:
        result["status"] = "UNSAT_SCHEMA" if result["legal_rows"] == 0 else "DIFFERENT" if result["distinguishing_rows"] else "EQUIVALENT"
    return result


def bounded_schema(schema_sql, enumeration) -> str:
    """Retain original constraints and add exactly the domains and fixed values."""
    schema = _schema_info(schema_sql)
    domains, fixed, _ = _enumeration(schema, enumeration)
    create = schema.expression.copy()
    for column in schema.columns:
        values = domains[column] if column in domains else [fixed[column]]
        _require(all(BIGINT_MIN <= value <= BIGINT_MAX for value in values), "Bounded schema literals must be in-range signed BIGINT integers")
        equalities = [exp.EQ(this=exp.column(column, quoted=True), expression=exp.Literal.number(value)) for value in values]
        create.this.append("expressions", exp.CheckColumnConstraint(this=exp.or_(*equalities)))
    return create.sql(dialect="duckdb")

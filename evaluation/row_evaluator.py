"""Evaluate production-encoded expressions on one explicitly supplied row."""

from typing import Literal, TypedDict

import z3

from .production import load_production


class EncodedRowResult(TypedDict):
    """Serializable row observations, distinct from query-equivalence verdicts."""

    status: Literal["EVALUATED", "INVALID_INPUT", "INCONCLUSIVE", "EVALUATION_ERROR"]
    schema_holds: bool | None
    predicate_truth: bool | None
    diagnostic: str | None


def _failure(status, diagnostic: str) -> EncodedRowResult:
    return {
        "status": status,
        "schema_holds": None,
        "predicate_truth": None,
        "diagnostic": diagnostic,
    }


def _validate_encoding(encoded: dict):
    """Check expression types, contexts and the declared column-symbol mapping."""
    if not isinstance(encoded, dict):
        raise ValueError("encode_query must return an EncodedQuery dictionary")
    required = ("columns", "schema_constraint", "predicate", "predicate_nodes")
    missing = [name for name in required if name not in encoded]
    if missing:
        raise ValueError(f"EncodedQuery is missing fields: {missing}")
    columns = encoded["columns"]
    if not isinstance(columns, dict) or not columns:
        raise ValueError("EncodedQuery columns must be a nonempty dictionary")
    if any(not isinstance(name, str) or not name for name in columns):
        raise ValueError("EncodedQuery column names must be nonempty strings")
    if type(encoded["predicate_nodes"]) is not int or encoded["predicate_nodes"] <= 0:
        raise ValueError("EncodedQuery predicate_nodes must be a positive integer")
    schema, predicate = encoded["schema_constraint"], encoded["predicate"]
    if not isinstance(schema, z3.BoolRef) or not isinstance(predicate, z3.BoolRef):
        raise ValueError("EncodedQuery schema_constraint and predicate must be Z3 Boolean expressions")
    context = schema.ctx
    if predicate.ctx != context:
        raise ValueError("EncodedQuery expressions use different Z3 contexts")
    symbol_ids = set()
    for name, symbol in columns.items():
        if (
            not isinstance(symbol, z3.ArithRef)
            or not z3.is_int(symbol)
            or not z3.is_const(symbol)
            or symbol.decl().kind() != z3.Z3_OP_UNINTERPRETED
        ):
            raise ValueError(f"EncodedQuery column {name!r} must map to a Z3 integer symbol")
        if symbol.ctx != context:
            raise ValueError(f"EncodedQuery column {name!r} uses a different Z3 context")
        if symbol.get_id() in symbol_ids:
            raise ValueError("Different declared columns must use distinct Z3 symbols")
        symbol_ids.add(symbol.get_id())

    pending, visited = [schema, predicate], set()
    while pending:
        expression = pending.pop()
        if expression.get_id() in visited:
            continue
        visited.add(expression.get_id())
        if z3.is_quantifier(expression):
            raise ValueError("EncodedQuery expressions must not contain quantified variables")
        if expression.decl().kind() == z3.Z3_OP_UNINTERPRETED:
            if not z3.is_const(expression) or expression.get_id() not in symbol_ids:
                raise ValueError("EncodedQuery expressions reference an undeclared column symbol or function")
        pending.extend(expression.children())
    return columns, schema, predicate, context


def _concrete_boolean(value, expression_name: str) -> bool:
    if z3.is_true(value):
        return True
    if z3.is_false(value):
        return False
    raise ValueError(f"Encoded {expression_name} remained symbolic after complete row bindings")


def evaluate_encoded_row(
    schema_sql: str,
    query_sql: str,
    row: dict[str, int],
    *,
    timeout_ms: int,
) -> EncodedRowResult:
    """Observe the real encoder's schema and predicate without asserting either.

    All declared columns are fixed to exact integer values. Out-of-range rows
    and CHECK-violating rows remain evaluable negative schema-legality cases.
    This function does not translate SQL or prove that the encoder is correct.
    """
    if type(timeout_ms) is not int or timeout_ms <= 0:
        return _failure("INVALID_INPUT", "timeout_ms must be a positive integer, not a boolean")
    if any(not isinstance(sql, str) or not sql.strip() for sql in (schema_sql, query_sql)):
        return _failure("INVALID_INPUT", "schema_sql and query_sql must be nonempty strings")
    if not isinstance(row, dict) or any(not isinstance(name, str) for name in row):
        return _failure("INVALID_INPUT", "row must be a dictionary with string column names")
    if any(type(value) is not int for value in row.values()):
        return _failure("INVALID_INPUT", "row values must be integers, excluding booleans and nulls")
    try:
        production = load_production(required=("encode_query", "InputError"))
        try:
            encoded = production.encode_query(schema_sql, query_sql)
        except production.InputError as error:
            return _failure("INVALID_INPUT", str(error) or "Production encoder rejected the SQL input")
        columns, schema, predicate, context = _validate_encoding(encoded)
        missing = sorted(set(columns) - row.keys())
        extra = sorted(row.keys() - set(columns))
        if missing or extra:
            return _failure("INVALID_INPUT", f"row column mismatch: missing={missing}, extra={extra}")
        solver = z3.Solver(ctx=context)
        solver.set(timeout=timeout_ms)
        solver.add(*(symbol == row[name] for name, symbol in columns.items()))
        outcome = solver.check()
        if outcome == z3.unknown:
            return _failure("INCONCLUSIVE", f"Fixed-row solver returned UNKNOWN: {solver.reason_unknown()}")
        if outcome != z3.sat:
            return _failure("EVALUATION_ERROR", "Complete integer row bindings unexpectedly failed to be SAT")
        model = solver.model()
        schema_holds = _concrete_boolean(model.eval(schema), "schema_constraint")
        predicate_truth = _concrete_boolean(model.eval(predicate), "predicate")
        return {
            "status": "EVALUATED",
            "schema_holds": schema_holds,
            "predicate_truth": predicate_truth,
            "diagnostic": None,
        }
    except Exception as error:
        return _failure("EVALUATION_ERROR", f"{type(error).__name__}: {error}")

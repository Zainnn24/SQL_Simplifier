"""Unit checks using explicit hand-built Z3 substitutes, not a SQL translator.

The patched encoder below tests the evaluation tool only. Passing these tests
does not validate the absent production SQL-to-Z3 implementation.
"""

from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import z3

from evaluation import EncodedRowResult, evaluate_encoded_row
from evaluation.production import ProductionUnavailable, load_production


SCHEMA = "CREATE TABLE t (row_id BIGINT NOT NULL, x BIGINT NOT NULL)"
QUERY = "SELECT row_id FROM t WHERE x >= 10"
MINIMUM, MAXIMUM = -(2**63), 2**63 - 1


class HandBuiltInputError(ValueError):
    """An explicitly test-only substitute for the verifier's InputError."""


def hand_built_encoding(*, context=None, check=None, predicate=None):
    """Create expressions directly; never parse or translate the supplied SQL."""
    context = context or z3.main_ctx()
    columns = {name: z3.Int(name, ctx=context) for name in ("row_id", "x")}
    constraints = [
        comparison
        for symbol in columns.values()
        for comparison in (symbol >= MINIMUM, symbol <= MAXIMUM)
    ]
    constraints.append(check(columns) if check else columns["x"] >= 0)
    return {
        "columns": columns,
        "schema_constraint": z3.And(*constraints),
        "predicate": predicate(columns) if predicate else columns["x"] >= 10,
        "predicate_nodes": 3,
    }


def hand_built_production(encoded):
    return SimpleNamespace(
        encode_query=Mock(return_value=encoded), InputError=HandBuiltInputError
    )


class ProductionLoadingTests(unittest.TestCase):
    def test_missing_module_reports_production_unavailable(self):
        with patch("evaluation.production.import_module", side_effect=ModuleNotFoundError("verifier")):
            with self.assertRaisesRegex(ProductionUnavailable, "Cannot import production verifier") as raised:
                load_production()
        self.assertEqual(raised.exception.missing, ("validate_input", "verify", "encode_query", "InputError"))

    def test_plan_only_namespace_reports_every_missing_api(self):
        with patch("evaluation.production.import_module", return_value=ModuleType("verifier")):
            with self.assertRaises(ProductionUnavailable) as raised:
                load_production()
        for name in ("validate_input", "verify", "encode_query", "InputError"):
            self.assertIn(name, str(raised.exception))
        self.assertEqual(raised.exception.missing, ("validate_input", "verify", "encode_query", "InputError"))

    def test_noncallable_export_and_wrong_error_class_are_rejected(self):
        for input_error in (None, RuntimeError, object(), "InputError"):
            with self.subTest(input_error=input_error):
                module = SimpleNamespace(encode_query=None, InputError=input_error)
                with patch("evaluation.production.import_module", return_value=module):
                    with self.assertRaises(ProductionUnavailable) as raised:
                        load_production(required=("encode_query", "InputError"))
                self.assertIn("encode_query", str(raised.exception))
                self.assertIn("InputError", str(raised.exception))
                self.assertEqual(raised.exception.missing, ("encode_query", "InputError"))

    def test_partial_api_is_allowed_only_when_explicitly_requested(self):
        module = hand_built_production(hand_built_encoding())
        with patch("evaluation.production.import_module", return_value=module) as importer:
            self.assertIs(load_production(required=("encode_query", "InputError")), module)
        importer.assert_called_once_with("verifier")


class EncodedRowEvaluatorTests(unittest.TestCase):
    def evaluate_substitute(self, encoded, row=None, timeout_ms=5000):
        production = hand_built_production(encoded)
        with patch("evaluation.row_evaluator.load_production", return_value=production) as loader:
            result = evaluate_encoded_row(
                SCHEMA, QUERY, {"row_id": 1, "x": 10} if row is None else row,
                timeout_ms=timeout_ms,
            )
        loader.assert_called_once_with(required=("encode_query", "InputError"))
        production.encode_query.assert_called_once_with(SCHEMA, QUERY)
        return result

    def assert_failure(self, result, status):
        self.assertEqual(result["status"], status, result)
        self.assertIsNone(result["schema_holds"])
        self.assertIsNone(result["predicate_truth"])
        self.assertTrue(result["diagnostic"])
        self.assertEqual(set(result), set(EncodedRowResult.__required_keys__))

    def test_public_result_type_and_true_false_predicate_observations(self):
        for value, expected in ((9, False), (10, True)):
            with self.subTest(value=value):
                result = self.evaluate_substitute(hand_built_encoding(), {"row_id": 1, "x": value})
                self.assertEqual(result, {
                    "status": "EVALUATED", "schema_holds": True,
                    "predicate_truth": expected, "diagnostic": None,
                })

    def test_check_violating_row_still_has_concrete_predicate_truth(self):
        encoded = hand_built_encoding(predicate=lambda columns: columns["x"] < 0)
        result = self.evaluate_substitute(encoded, {"row_id": 1, "x": -1})
        self.assertEqual(result, {
            "status": "EVALUATED", "schema_holds": False,
            "predicate_truth": True, "diagnostic": None,
        })

    def test_supported_contradictory_schema_is_evaluated_without_preflight(self):
        encoded = hand_built_encoding(
            check=lambda columns: z3.And(columns["x"] < 0, columns["x"] >= 0)
        )
        result = self.evaluate_substitute(encoded)
        self.assertEqual(result["status"], "EVALUATED")
        self.assertIs(result["schema_holds"], False)
        self.assertIs(result["predicate_truth"], True)

    def test_custom_z3_context_is_preserved(self):
        result = self.evaluate_substitute(hand_built_encoding(context=z3.Context()))
        self.assertEqual(result["status"], "EVALUATED", result)
        self.assertIs(result["schema_holds"], True)
        self.assertIs(result["predicate_truth"], True)

    def test_bigint_endpoints_adjacent_values_and_out_of_range_rows(self):
        for value, legal in (
            (MINIMUM - 1, False), (MINIMUM, True), (MINIMUM + 1, True),
            (MAXIMUM - 1, True), (MAXIMUM, True), (MAXIMUM + 1, False),
        ):
            with self.subTest(value=value):
                encoded = hand_built_encoding(check=lambda columns: z3.BoolVal(True, ctx=columns["x"].ctx))
                result = self.evaluate_substitute(encoded, {"row_id": 1, "x": value})
                self.assertEqual(result["status"], "EVALUATED", result)
                self.assertIs(result["schema_holds"], legal)
                self.assertIs(result["predicate_truth"], value >= 10)

    def test_missing_and_extra_columns_are_invalid_input(self):
        for row in ({"x": 10}, {"row_id": 1}, {}, {"row_id": 1, "x": 10, "extra": 0}):
            with self.subTest(row=row):
                self.assert_failure(self.evaluate_substitute(hand_built_encoding(), row), "INVALID_INPUT")

    def test_invalid_row_types_values_and_column_names(self):
        for row in (
            None, [], [("row_id", 1), ("x", 10)],
            {"row_id": 1, "x": True}, {"row_id": 1, "x": None},
            {"row_id": 1, "x": 10.0}, {"row_id": 1, "x": "10"}, {1: 1, "x": 10},
        ):
            with self.subTest(row=row):
                with patch("evaluation.row_evaluator.load_production") as loader:
                    result = evaluate_encoded_row(SCHEMA, QUERY, row, timeout_ms=5000)
                self.assert_failure(result, "INVALID_INPUT")
                loader.assert_not_called()

    def test_timeout_must_be_positive_integer_excluding_bool(self):
        for timeout in (True, False, 0, -1, None, 5.0, "5"):
            with self.subTest(timeout=timeout):
                with patch("evaluation.row_evaluator.load_production") as loader:
                    result = evaluate_encoded_row(SCHEMA, QUERY, {"row_id": 1, "x": 10}, timeout_ms=timeout)
                self.assert_failure(result, "INVALID_INPUT")
                loader.assert_not_called()

    def test_production_input_error_is_invalid_input(self):
        production = hand_built_production(hand_built_encoding())
        production.encode_query.side_effect = HandBuiltInputError("Unsupported arithmetic")
        with patch("evaluation.row_evaluator.load_production", return_value=production):
            result = evaluate_encoded_row(SCHEMA, QUERY, {"row_id": 1, "x": 10}, timeout_ms=5000)
        self.assert_failure(result, "INVALID_INPUT")
        self.assertIn("Unsupported arithmetic", result["diagnostic"])

    def test_production_unavailable_is_explicit_evaluation_error(self):
        with patch("evaluation.row_evaluator.load_production", side_effect=ProductionUnavailable("Missing encode_query")):
            result = evaluate_encoded_row(SCHEMA, QUERY, {"row_id": 1, "x": 10}, timeout_ms=5000)
        self.assert_failure(result, "EVALUATION_ERROR")
        self.assertIn("ProductionUnavailable", result["diagnostic"])
        self.assertIn("encode_query", result["diagnostic"])

    def test_unexpected_encoder_failure_is_evaluation_error(self):
        production = hand_built_production(hand_built_encoding())
        production.encode_query.side_effect = RuntimeError("Unexpected encoder failure")
        with patch("evaluation.row_evaluator.load_production", return_value=production):
            result = evaluate_encoded_row(SCHEMA, QUERY, {"row_id": 1, "x": 10}, timeout_ms=5000)
        self.assert_failure(result, "EVALUATION_ERROR")
        self.assertIn("RuntimeError", result["diagnostic"])

    def test_unknown_solver_result_is_inconclusive(self):
        solver = Mock()
        solver.check.return_value = z3.unknown
        solver.reason_unknown.return_value = "Test-only forced timeout"
        with patch("evaluation.row_evaluator.z3.Solver", return_value=solver):
            result = self.evaluate_substitute(hand_built_encoding())
        self.assert_failure(result, "INCONCLUSIVE")
        self.assertIn("Test-only forced timeout", result["diagnostic"])
        solver.set.assert_called_once_with(timeout=5000)
        solver.model.assert_not_called()

    def test_unexpected_solver_exception_is_evaluation_error(self):
        solver = Mock()
        solver.check.side_effect = RuntimeError("Test-only solver failure")
        with patch("evaluation.row_evaluator.z3.Solver", return_value=solver):
            result = self.evaluate_substitute(hand_built_encoding())
        self.assert_failure(result, "EVALUATION_ERROR")

    def test_symbolic_observation_is_not_filled_using_model_completion(self):
        solver = Mock()
        solver.check.return_value = z3.sat
        model = solver.model.return_value
        model.eval.side_effect = [z3.BoolVal(True), z3.Bool("test_only_unresolved")]
        with patch("evaluation.row_evaluator.z3.Solver", return_value=solver):
            result = self.evaluate_substitute(hand_built_encoding())
        self.assert_failure(result, "EVALUATION_ERROR")
        self.assertIn("remained symbolic", result["diagnostic"])
        self.assertTrue(all(not call.kwargs for call in model.eval.call_args_list))

    def test_malformed_encoded_objects_and_column_mapping_are_rejected(self):
        malformed = []
        encoded = hand_built_encoding()
        malformed.extend((None, {}, {**encoded, "predicate": True}))
        malformed.append({**encoded, "predicate_nodes": True})
        malformed.append({**encoded, "columns": {"row_id": z3.IntVal(1), "x": encoded["columns"]["x"]}})
        malformed.append({**encoded, "columns": {"row_id": z3.Real("row_id"), "x": encoded["columns"]["x"]}})
        malformed.append({**encoded, "columns": {"row_id": encoded["columns"]["x"], "x": encoded["columns"]["x"]}})
        for item in malformed:
            with self.subTest(encoded=item):
                self.assert_failure(self.evaluate_substitute(item), "EVALUATION_ERROR")

    def test_mixed_context_expressions_and_columns_are_rejected(self):
        encoded = hand_built_encoding()
        other = z3.Context()
        mixed_predicate = {**encoded, "predicate": z3.BoolVal(True, ctx=other)}
        mixed_columns = {**encoded, "columns": {"row_id": z3.Int("row_id", ctx=other), "x": encoded["columns"]["x"]}}
        for item in (mixed_predicate, mixed_columns):
            with self.subTest(encoded=item):
                result = self.evaluate_substitute(item)
                self.assert_failure(result, "EVALUATION_ERROR")
                self.assertIn("context", result["diagnostic"])

    def test_expression_using_undeclared_symbol_is_rejected(self):
        encoded = hand_built_encoding()
        encoded["predicate"] = z3.Int("undeclared_test_column") >= 0
        result = self.evaluate_substitute(encoded)
        self.assert_failure(result, "EVALUATION_ERROR")
        self.assertIn("undeclared", result["diagnostic"])


if __name__ == "__main__":
    unittest.main()

"""Independent concrete oracle tests; no production or substitute translator."""

from collections import Counter
import unittest
from unittest.mock import MagicMock, patch

import duckdb

from evaluation.duckdb_oracle import (
    BIGINT_MAX, BIGINT_MIN, ScopeError, bounded_schema, enumerate_case,
    observe_row, schema_columns, validate_pair,
)


SCHEMA = "CREATE TABLE t (row_id BIGINT NOT NULL, x BIGINT NOT NULL, y BIGINT NOT NULL)"
ORIGINAL = "SELECT row_id FROM t WHERE x >= 0"
CANDIDATE = "SELECT row_id FROM t WHERE x > 0"


def make_case(original=ORIGINAL, candidate=CANDIDATE, schema=SCHEMA, enumeration=None):
    return {
        "case_id": "local-independent-case", "schema_sql": schema,
        "original_sql": original, "candidate_sql": candidate,
        "expected_status": "DIFFERENT", "justification": "Independent local test fixture.",
        "counterexample": None,
        "enumeration": enumeration if enumeration is not None else {
            "domains": {"x": [-2, -1, 0, 1, 2], "y": [-2, -1, 0, 1, 2]},
            "fixed_values": {"row_id": 1},
        },
    }


class ScopeTests(unittest.TestCase):
    def test_columns_and_contradictory_checks_are_supported(self):
        schema = "CREATE TABLE t (x BIGINT NOT NULL, y BIGINT NOT NULL, CHECK (x < 0), CHECK (x >= 0))"
        self.assertEqual(schema_columns(schema), ("x", "y"))
        self.assertIsNone(validate_pair(schema, "SELECT y,x FROM t WHERE FALSE", "SELECT y,x FROM t WHERE TRUE"))

    def test_source_scope_controls(self):
        for schema in (
            "CREATE TABLE t (row_id BIGINT NOT NULL, x BIGINT, y BIGINT NOT NULL)",
            "CREATE TABLE t (row_id BIGINT NOT NULL, x INTEGER NOT NULL, y BIGINT NOT NULL)",
            "CREATE TABLE t (x BIGINT NULL)",
            "CREATE TABLE t (x BIGINT NOT NULL UNIQUE)",
            "CREATE TABLE t (x BIGINT NOT NULL DEFAULT 0)",
            "CREATE TABLE t (x BIGINT NOT NULL, CHECK (x + 1 > 0))",
            "CREATE TABLE t (x BIGINT NOT NULL, CHECK (missing > 0))",
            "CREATE TABLE t (x BIGINT NOT NULL); CREATE TABLE u (x BIGINT NOT NULL)",
        ):
            with self.subTest(schema=schema):
                self.assertEqual(validate_pair(schema, ORIGINAL, CANDIDATE), "INVALID_INPUT")
        for sql in ("SELECT row_id FROM t", "SELECT missing FROM t WHERE TRUE", "not sql"):
            self.assertEqual(validate_pair(SCHEMA, sql, CANDIDATE), "INVALID_INPUT")

    def test_candidate_scope_controls(self):
        for sql in (
            "SELECT x FROM t WHERE TRUE", "SELECT row_id FROM u WHERE TRUE",
            "SELECT row_id FROM t WHERE x + 1 > 0", "SELECT row_id FROM t WHERE CAST(x AS BIGINT) > 0",
            "SELECT row_id FROM t WHERE x IS NULL", "SELECT row_id FROM t WHERE x BETWEEN 0 AND 1",
            "SELECT row_id FROM t WHERE x > 0 LIMIT 1", "SELECT row_id FROM t WHERE TRUE ORDER BY x",
            "SELECT DISTINCT row_id FROM t WHERE TRUE", "SELECT count(*) FROM t WHERE TRUE",
            "SELECT row_id FROM t WHERE x IN (SELECT x FROM t)",
            "SELECT t.row_id FROM t JOIN t AS u ON t.x=u.x WHERE TRUE",
            "SELECT row_id FROM t WHERE TRUE; SELECT row_id FROM t WHERE FALSE",
            f"SELECT row_id FROM t WHERE x < {BIGINT_MAX + 1}",
        ):
            with self.subTest(sql=sql):
                self.assertEqual(validate_pair(SCHEMA, ORIGINAL, sql), "INVALID_CANDIDATE")

    def test_ordered_multicolumn_projection_and_case(self):
        original = "SELECT x,row_id FROM t WHERE TRUE"
        self.assertIsNone(validate_pair(SCHEMA, original, "SELECT T.X,T.ROW_ID FROM T WHERE FALSE"))
        self.assertEqual(validate_pair(SCHEMA, original, "SELECT row_id,x FROM t WHERE TRUE"), "INVALID_CANDIDATE")


class ObservationTests(unittest.TestCase):
    def test_truth_is_observed_even_when_full_query_filters_row(self):
        result = observe_row(SCHEMA, ORIGINAL, {"row_id": 8, "x": -1, "y": 0})
        self.assertEqual(result, {"status": "OBSERVED", "schema_holds": True,
                                 "predicate_truth": False, "rows": [], "diagnostic": None})

    def test_full_projection_preserves_column_order_and_duplicate_columns(self):
        result = observe_row(SCHEMA, "SELECT y,x,y FROM t WHERE TRUE", {"y": 9, "row_id": 1, "x": 2})
        self.assertEqual(result["rows"], [[9, 2, 9]])

    def test_original_where_text_comments_negation_and_precedence(self):
        sql = "SELECT row_id FROM t WHERE NOT (x < -2) AND (y = 0 OR x > 4); -- trailing comment"
        result = observe_row(SCHEMA, sql, {"row_id": 1, "x": -2, "y": 0})
        self.assertEqual(result["status"], "OBSERVED")
        self.assertTrue(result["predicate_truth"])
        self.assertEqual(result["rows"], [[1]])

    def test_checks_are_decided_by_actual_insert(self):
        schema = SCHEMA[:-1] + ", CHECK (x <= y), CHECK (x >= 0))"
        good = observe_row(schema, ORIGINAL, {"row_id": 1, "x": 0, "y": 0})
        bad = observe_row(schema, ORIGINAL, {"row_id": 1, "x": 1, "y": 0})
        self.assertEqual(good["status"], "OBSERVED")
        self.assertEqual(bad["status"], "REJECTED_CHECK")
        self.assertFalse(bad["schema_holds"])
        self.assertIsNone(bad["predicate_truth"])
        self.assertIsNone(bad["rows"])

    def test_full_width_endpoints_and_actual_type_rejection(self):
        for value in (BIGINT_MIN, BIGINT_MIN + 1, BIGINT_MAX - 1, BIGINT_MAX):
            result = observe_row(SCHEMA, "SELECT x FROM t WHERE TRUE", {"row_id": 1, "x": value, "y": 0})
            self.assertEqual(result["status"], "OBSERVED")
            self.assertEqual(result["rows"], [[value]])
        for value in (BIGINT_MIN - 1, BIGINT_MAX + 1, 2**100, 2**1000, -(2**1000)):
            result = observe_row(SCHEMA, ORIGINAL, {"row_id": 1, "x": value, "y": 0})
            self.assertEqual(result["status"], "REJECTED_TYPE")
            self.assertFalse(result["schema_holds"])
            self.assertIsNone(result["predicate_truth"])

    def test_rows_require_exact_complete_integer_columns(self):
        for row in ({"row_id": 1, "x": 0}, {"row_id": 1, "x": 0, "y": 1, "extra": 0},
                    {"row_id": 1, "x": True, "y": 0}, {"row_id": 1, "x": None, "y": 0}):
            self.assertEqual(observe_row(SCHEMA, ORIGINAL, row)["status"], "INVALID_INPUT")

    def test_sql_error_is_not_illegality_or_infrastructure(self):
        connection = MagicMock()
        connection.execute.side_effect = duckdb.BinderException("unexpected binding failure")
        with patch("evaluation.duckdb_oracle.duckdb.connect", return_value=connection):
            result = observe_row(SCHEMA, ORIGINAL, {"row_id": 1, "x": 0, "y": 0})
        self.assertEqual(result["status"], "SQL_ERROR")
        self.assertIsNone(result["schema_holds"])
        connection.close.assert_called_once()

    def test_connect_and_execution_infrastructure_failures_stay_visible(self):
        with patch("evaluation.duckdb_oracle.duckdb.connect", side_effect=OSError("unavailable")):
            self.assertEqual(observe_row(SCHEMA, ORIGINAL, {"row_id": 1, "x": 0, "y": 0})["status"], "INFRASTRUCTURE_ERROR")
        connection = MagicMock()
        connection.execute.side_effect = duckdb.IOException("storage unavailable")
        with patch("evaluation.duckdb_oracle.duckdb.connect", return_value=connection):
            result = observe_row(SCHEMA, ORIGINAL, {"row_id": 1, "x": 0, "y": 0})
        self.assertEqual(result["status"], "INFRASTRUCTURE_ERROR")
        self.assertIsNone(result["schema_holds"])

    def test_close_failure_is_reported_instead_of_escaping(self):
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = (True,)
        connection.execute.return_value.fetchall.return_value = [(1,)]
        connection.close.side_effect = OSError("close unavailable")
        with patch("evaluation.duckdb_oracle.duckdb.connect", return_value=connection):
            result = observe_row(SCHEMA, ORIGINAL, {"row_id": 1, "x": 0, "y": 0})
        self.assertEqual(result["status"], "INFRASTRUCTURE_ERROR")
        self.assertTrue(result["schema_holds"])
        self.assertIn("close:", result["diagnostic"])

    def test_unexpected_insert_error_is_not_hidden_by_out_of_range_row(self):
        connection = MagicMock()
        connection.execute.side_effect = [connection, duckdb.BinderException("unexpected binding failure")]
        with patch("evaluation.duckdb_oracle.duckdb.connect", return_value=connection):
            result = observe_row(SCHEMA, ORIGINAL, {"row_id": 1, "x": BIGINT_MAX + 1, "y": 0})
        self.assertEqual(result["status"], "SQL_ERROR")
        self.assertIsNone(result["schema_holds"])


class EnumerationTests(unittest.TestCase):
    def test_cartesian_product_has_25_rows_not_five_diagonal_rows(self):
        result = enumerate_case(make_case())
        self.assertEqual((result["total_rows"], result["legal_rows"], result["distinguishing_rows"]), (25, 25, 5))
        self.assertEqual(result["status"], "DIFFERENT")
        rows = result["observations"]
        self.assertEqual(len(rows), 25)
        self.assertIn({"row_id": 1, "x": -2, "y": 2}, [observation["row"] for observation in rows])
        self.assertEqual(result["errors"], [])

    def test_bounded_agreement_is_distinct_from_unrestricted_label(self):
        case = make_case(original="SELECT row_id FROM t WHERE x < 10", candidate="SELECT row_id FROM t WHERE TRUE")
        self.assertEqual(case["expected_status"], "DIFFERENT")
        self.assertEqual(enumerate_case(case)["status"], "EQUIVALENT")
        self.assertFalse(observe_row(SCHEMA, case["original_sql"], {"row_id": 1, "x": 10, "y": 0})["predicate_truth"])

    def test_duplicate_projected_rows_are_retained(self):
        case = make_case(original="SELECT y FROM t WHERE x >= 0", candidate="SELECT y FROM t WHERE x > 0")
        case["enumeration"] = {"domains": {"x": [0, 1]}, "fixed_values": {"row_id": 1, "y": 7}}
        result = enumerate_case(case)
        self.assertEqual(Counter(map(tuple, result["original_rows"])), Counter({(7,): 2}))
        self.assertEqual(result["candidate_rows"], [[7]])

    def test_contradictory_and_empty_bounded_schemas(self):
        schema = SCHEMA[:-1] + ", CHECK (x < 0), CHECK (x >= 0))"
        result = enumerate_case(make_case(schema=schema))
        self.assertEqual((result["status"], result["total_rows"], result["legal_rows"]), ("UNSAT_SCHEMA", 25, 0))
        self.assertEqual(result["errors"], [])
        case = make_case(schema=SCHEMA[:-1] + ", CHECK (x > 100))")
        self.assertEqual(enumerate_case(case)["status"], "UNSAT_SCHEMA")

    def test_false_original_with_legal_schema_remains_equivalent(self):
        result = enumerate_case(make_case(original="SELECT row_id FROM t WHERE FALSE", candidate="SELECT row_id FROM t WHERE FALSE"))
        self.assertEqual(result["status"], "EQUIVALENT")
        self.assertEqual(result["legal_rows"], 25)

    def test_invalid_enumeration_maps_and_size_limit(self):
        for enumeration in (
            None, {"domains": {"x": [0]}, "fixed_values": {"row_id": 1}},
            {"domains": {"x": [0]}, "fixed_values": {"row_id": 1, "x": 0, "y": 0}},
            {"domains": {"x": []}, "fixed_values": {"row_id": 1, "y": 0}},
            {"domains": {"x": [0, 0]}, "fixed_values": {"row_id": 1, "y": 0}},
            {"domains": {"x": [False]}, "fixed_values": {"row_id": 1, "y": 0}},
            {"domains": {"x": list(range(101)), "y": list(range(100))}, "fixed_values": {"row_id": 1}},
        ):
            case = make_case()
            case["enumeration"] = enumeration
            with self.subTest(enumeration=enumeration):
                self.assertEqual(enumerate_case(case)["status"], "INVALID_INPUT")

    def test_scope_controls_and_runtime_failures_cannot_look_equivalent(self):
        self.assertEqual(enumerate_case(make_case(candidate="SELECT x FROM t WHERE TRUE"))["status"], "INVALID_CANDIDATE")
        with patch("evaluation.duckdb_oracle.duckdb.connect", side_effect=OSError("unavailable")):
            result = enumerate_case(make_case())
        self.assertEqual(result["status"], "INFRASTRUCTURE_ERROR")
        self.assertEqual(result["legal_rows"], 0)
        self.assertTrue(result["errors"])

    def test_enumeration_and_bounded_schema_share_the_fixed_row_limit(self):
        case = make_case(enumeration={
            "domains": {"x": list(range(101)), "y": list(range(100))},
            "fixed_values": {"row_id": 1},
        })
        with patch("evaluation.duckdb_oracle.observe_row", side_effect=AssertionError("Oversized domains must not run")) as observe:
            self.assertEqual(enumerate_case(case)["status"], "INVALID_INPUT")
            case["max_enumeration_rows"] = 20_000
            self.assertEqual(enumerate_case(case)["status"], "INVALID_INPUT")
            observe.assert_not_called()
        with self.assertRaisesRegex(ScopeError, "exceeding limit 10000"):
            bounded_schema(case["schema_sql"], case["enumeration"])


class BoundedSchemaTests(unittest.TestCase):
    def test_original_checks_domains_and_all_fixed_columns_are_retained(self):
        schema = SCHEMA[:-1] + ", CHECK (x <= y))"
        enumeration = {"domains": {"x": [-2, 0, 2]}, "fixed_values": {"row_id": 7, "y": 0}}
        bounded = bounded_schema(schema, enumeration)
        self.assertEqual(schema_columns(bounded), ("row_id", "x", "y"))
        for row, status in (
            ({"row_id": 7, "x": -2, "y": 0}, "OBSERVED"),
            ({"row_id": 7, "x": 2, "y": 0}, "REJECTED_CHECK"),
            ({"row_id": 7, "x": -1, "y": 0}, "REJECTED_CHECK"),
            ({"row_id": 8, "x": 0, "y": 0}, "REJECTED_CHECK"),
            ({"row_id": 7, "x": 0, "y": 1}, "REJECTED_CHECK"),
        ):
            self.assertEqual(observe_row(bounded, "SELECT row_id FROM t WHERE TRUE", row)["status"], status)

    def test_out_of_range_row_values_cannot_be_sql_domain_literals(self):
        enumeration = {"domains": {"x": [BIGINT_MAX + 1]}, "fixed_values": {"row_id": 1, "y": 0}}
        with self.assertRaises(ScopeError):
            bounded_schema(SCHEMA, enumeration)


if __name__ == "__main__":
    unittest.main()

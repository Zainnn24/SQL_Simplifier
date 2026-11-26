"""Driver regressions with explicit hand-built encodings and scripted verdicts.

These substitutes cover only the named test examples, never translate SQL, and
are injected solely with test patches. They are not production-verifier evidence.
"""

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import z3

from evaluation.verifier_check import (
    load_fixtures, main, production_not_run, run_calibration, run_offline,
    run_production, run_production_suite,
)
from evaluation.verifier_probes import (
    BIGINT_MAX, BIGINT_MIN, CALIBRATION_CASES, CALIBRATION_E2_QUERY,
    CALIBRATION_E2_SCHEMA, PROBE_QUERY, PROBE_SCHEMA,
    independent_type_bound_probes, run_type_bound_probes,
)


class HandBuiltInputError(ValueError):
    """Test-only substitute for the production-owned exception class."""


def fixture_case():
    return {
        "case_id": "test-bound-probe-driver", "schema_sql": PROBE_SCHEMA,
        "original_sql": PROBE_QUERY, "candidate_sql": "SELECT row_id FROM t WHERE x > 0",
        "expected_status": "DIFFERENT", "justification": "At x = 0 only the inclusive bound is true.",
        "counterexample": {"row": {"row_id": 1, "x": 0}, "original_truth": True,
                           "candidate_truth": False, "original_rows": [[1]], "candidate_rows": []},
        "enumeration": {"domains": {"x": [-1, 0, 1]}, "fixed_values": {"row_id": 1}},
    }


def hand_built_api(*, omit_bounds=(), ready_nodes=3, verdict_original_nodes=3, wrong_e2_candidate_counts=False):
    """Recognize only fixed test requests and build their Z3 expressions by hand."""
    def encode(schema_sql, query_sql):
        if schema_sql != PROBE_SCHEMA or query_sql not in (PROBE_QUERY, "SELECT row_id FROM t WHERE x > 0"):
            raise AssertionError("The hand-built test encoder received an unspecified example")
        columns = {name: z3.Int(name) for name in ("row_id", "x")}
        bounds = [
            comparison for name, symbol in columns.items() if name not in omit_bounds
            for comparison in (symbol >= BIGINT_MIN, symbol <= BIGINT_MAX)
        ]
        return {
            "columns": columns, "schema_constraint": z3.And(*bounds),
            "predicate": columns["x"] >= 0 if query_sql == PROBE_QUERY else columns["x"] > 0,
            "predicate_nodes": 3,
        }

    def validate(schema_sql, original_sql, *, timeout_ms):
        nodes = 7 if original_sql == CALIBRATION_E2_QUERY else ready_nodes
        return {"status": "READY", "original_nodes": nodes, "diagnostic": None}

    def verify(*, schema_sql, original_sql, candidate_sql, timeout_ms):
        if original_sql == CALIBRATION_E2_QUERY:
            if schema_sql != CALIBRATION_E2_SCHEMA:
                raise AssertionError("Unexpected E2 schema in scripted calibration")
            candidate_nodes = 7 if candidate_sql == CALIBRATION_E2_QUERY else 3
            if wrong_e2_candidate_counts:
                candidate_nodes = 6 if candidate_nodes == 7 else 4
            return {"status": "EQUIVALENT", "original_nodes": 7, "candidate_nodes": candidate_nodes,
                    "diagnostic": None, "witness": None}
        if original_sql != PROBE_QUERY or candidate_sql != "SELECT row_id FROM t WHERE x > 0":
            raise AssertionError("The scripted verifier received an unspecified example")
        return {"status": "DIFFERENT", "original_nodes": verdict_original_nodes, "candidate_nodes": 3,
                "diagnostic": None, "witness": fixture_case()["counterexample"]}

    return SimpleNamespace(
        validate_input=Mock(side_effect=validate), verify=Mock(side_effect=verify),
        encode_query=Mock(side_effect=encode), InputError=HandBuiltInputError,
    )


class TypeBoundProbeTests(unittest.TestCase):
    def test_offline_insert_expectations_cover_both_columns_without_sql_overflow_literals(self):
        report = independent_type_bound_probes()
        self.assertEqual(report["probe_count"], 8)
        self.assertEqual(report["errors"], [])
        for column in ("row_id", "x"):
            probes = [probe for probe in report["probes"] if probe["column"] == column]
            self.assertEqual({probe["value"] for probe in probes}, {BIGINT_MIN - 1, BIGINT_MIN, BIGINT_MAX, BIGINT_MAX + 1})
            for probe in probes:
                legal = probe["value"] in (BIGINT_MIN, BIGINT_MAX)
                self.assertIs(probe["expected_schema_holds"], legal)
                self.assertEqual(probe["expected"]["status"], "OBSERVED" if legal else "REJECTED_TYPE")
                self.assertEqual(probe["schema_sql"], PROBE_SCHEMA)
                self.assertEqual(probe["query_sql"], PROBE_QUERY)
                if not legal:
                    self.assertIsNone(probe["expected"]["predicate_truth"])
                    self.assertNotIn(str(probe["value"]), probe["schema_sql"] + probe["query_sql"])

    def test_separate_probes_do_not_change_the_182_fixture_assignments(self):
        path = Path(__file__).resolve().parents[1] / "verifier_cases.jsonl"
        report = run_offline(load_fixtures(path))
        self.assertEqual(report["summary"]["enumerated_rows"], 182)
        self.assertEqual(report["summary"]["type_bound_probes"], 8)
        self.assertEqual(report["summary"]["type_bound_probe_errors"], 0)

    def test_actual_row_evaluator_agrees_with_all_eight_expectations(self):
        api = hand_built_api()
        with patch("evaluation.row_evaluator.load_production", return_value=api):
            report = run_production_suite([], run_offline([]), api, 5000)
        self.assertEqual(report["discrepancies"], [])
        self.assertEqual(report["metrics"]["type_bound_probes"], 8)
        self.assertEqual(report["metrics"]["row_checks"], 0)
        self.assertEqual(report["metrics"]["type_bound_mismatches"], 0)
        self.assertEqual(api.encode_query.call_count, 8)

    def test_driver_detects_bounds_missing_only_from_projection_column(self):
        self.assert_missing_bounds_detected("row_id")

    def test_driver_detects_bounds_missing_only_from_predicate_column(self):
        self.assert_missing_bounds_detected("x")

    def assert_missing_bounds_detected(self, column):
        api = hand_built_api(omit_bounds=(column,))
        with patch("evaluation.row_evaluator.load_production", return_value=api):
            report = run_production_suite([], run_offline([]), api, 5000)
        mismatches = [item for item in report["discrepancies"] if item["stage"] == "type_bound_probe"]
        self.assertEqual(report["metrics"]["type_bound_mismatches"], 2)
        self.assertEqual({item["inputs"]["column"] for item in mismatches}, {column})
        self.assertEqual({item["inputs"]["value"] for item in mismatches}, {BIGINT_MIN - 1, BIGINT_MAX + 1})
        for mismatch in mismatches:
            self.assertIs(mismatch["expected"]["schema_holds"], False)
            self.assertIs(mismatch["actual"]["schema_holds"], True)
            self.assertEqual(mismatch["inputs"]["row"][column], mismatch["inputs"]["value"])

    def test_probe_unknown_and_errors_are_visible_separately_from_mismatches(self):
        expectations = independent_type_bound_probes()
        for status, metric in (("INCONCLUSIVE", "type_bound_inconclusive"), ("EVALUATION_ERROR", "type_bound_errors")):
            with self.subTest(status=status):
                evaluate = Mock(return_value={"status": status, "schema_holds": None, "predicate_truth": None, "diagnostic": "Scripted probe failure"})
                report = run_type_bound_probes(expectations, evaluate, 5000)
                self.assertEqual(report["metrics"][metric], 8)
                self.assertEqual(report["metrics"]["type_bound_mismatches"], 0)
                self.assertEqual(len(report["discrepancies"]), 8)


class NodeCountCalibrationTests(unittest.TestCase):
    def test_three_known_examples_call_validation_and_verification_directly(self):
        api = hand_built_api()
        report = run_calibration(api, 5000)
        self.assertEqual(report["discrepancies"], [])
        self.assertEqual(report["metrics"]["calibration_checks"], 3)
        self.assertEqual(report["metrics"]["calibration_validation_checks"], 3)
        self.assertEqual(report["metrics"]["calibration_node_mismatches"], 0)
        self.assertEqual((api.validate_input.call_count, api.verify.call_count), (3, 3))
        self.assertEqual([record["expected_nodes"] for record in report["cases"]], [
            {"original_nodes": 3, "candidate_nodes": 3},
            {"original_nodes": 7, "candidate_nodes": 3},
            {"original_nodes": 7, "candidate_nodes": 7},
        ])
        self.assertEqual([call.kwargs["candidate_sql"] for call in api.verify.call_args_list], [case["candidate_sql"] for case in CALIBRATION_CASES])

    def test_correct_verdicts_and_witnesses_do_not_hide_full_and_bounded_count_disagreement(self):
        case = fixture_case()
        api = hand_built_api(verdict_original_nodes=4)
        with patch("evaluation.row_evaluator.load_production", return_value=api):
            report = run_production([case], run_offline([case]), api, 5000)
        self.assertEqual(report["metrics"]["node_count_mismatches"], 2)
        self.assertEqual(report["metrics"]["verdict_mismatches"], 0)
        self.assertEqual(report["metrics"]["confirmed_replays"], 2)
        self.assertEqual({item["stage"] for item in report["discrepancies"]}, {"full_range_node_count", "bounded_node_count"})
        for item in report["discrepancies"]:
            self.assertEqual(item["case_id"], case["case_id"])
            self.assertEqual(item["expected"], {"original_nodes": 3})
            self.assertEqual(item["actual"], {"original_nodes": 4})
            self.assertEqual(item["inputs"]["original_sql"], case["original_sql"])

    def test_independent_calibration_catches_consistent_but_wrong_preflight_and_verdict_counts(self):
        api = hand_built_api(ready_nodes=4, verdict_original_nodes=4)
        report = run_calibration(api, 5000)
        self.assertEqual(report["metrics"]["calibration_node_mismatches"], 2)
        self.assertEqual(report["metrics"]["calibration_errors"], 0)
        self.assertEqual({item["inputs"]["field"] for item in report["discrepancies"]}, {"validation.original_nodes", "verify.original_nodes"})
        self.assertEqual({item["case_id"] for item in report["discrepancies"]}, {"calibration-boundary-3-3"})

    def test_independent_calibration_checks_candidate_counts_for_simplification_and_copy(self):
        report = run_calibration(hand_built_api(wrong_e2_candidate_counts=True), 5000)
        self.assertEqual(report["metrics"]["calibration_node_mismatches"], 2)
        self.assertEqual({item["case_id"] for item in report["discrepancies"]}, {"calibration-e2-7-3", "calibration-e2-copy-7-7"})
        self.assertTrue(all(item["inputs"]["field"] == "verify.candidate_nodes" for item in report["discrepancies"]))


class FormalProbeEntryPointTests(unittest.TestCase):
    def run_cli_with_hand_built_api(self, api):
        with TemporaryDirectory() as directory:
            fixtures, output = Path(directory) / "cases.jsonl", Path(directory) / "report.json"
            fixtures.write_text(json.dumps(fixture_case()) + "\n", encoding="utf-8")
            with (
                patch("evaluation.verifier_check.load_production", return_value=api),
                patch("evaluation.row_evaluator.load_production", return_value=api),
                redirect_stdout(StringIO()),
            ):
                status = main(["--fixtures", str(fixtures), "--output", str(output)])
            return status, json.loads(output.read_text(encoding="utf-8"))

    def test_formal_entry_point_runs_all_separate_checks_with_correct_substitutes(self):
        api = hand_built_api()
        status, report = self.run_cli_with_hand_built_api(api)
        self.assertEqual(status, 0, report)
        metrics = report["production"]["metrics"]
        self.assertEqual((metrics["row_checks"], metrics["type_bound_probes"], metrics["calibration_checks"]), (6, 8, 3))
        self.assertEqual(api.verify.call_count, 5)
        self.assertEqual(api.validate_input.call_count, 4)
        self.assertEqual(api.encode_query.call_count, 14)
        self.assertEqual(report["production"]["discrepancies"], [])

    def test_formal_entry_point_fails_for_each_individually_missing_column_bound(self):
        for column in ("row_id", "x"):
            with self.subTest(column=column):
                status, report = self.run_cli_with_hand_built_api(hand_built_api(omit_bounds=(column,)))
                self.assertEqual(status, 1)
                production = report["production"]
                self.assertEqual(production["metrics"]["row_mismatches"], 0)
                self.assertEqual(production["metrics"]["type_bound_mismatches"], 2)
                self.assertEqual({item["inputs"]["column"] for item in production["discrepancies"]}, {column})

    def test_formal_entry_point_fails_on_wrong_counts_despite_correct_verdicts(self):
        status, report = self.run_cli_with_hand_built_api(hand_built_api(verdict_original_nodes=4))
        self.assertEqual(status, 1)
        metrics = report["production"]["metrics"]
        self.assertEqual(metrics["verdict_mismatches"], 0)
        self.assertEqual(metrics["node_count_mismatches"], 2)
        self.assertEqual(metrics["calibration_node_mismatches"], 1)

    def test_unavailable_production_never_reports_zero_probe_or_calibration_failures(self):
        report = production_not_run("Missing production APIs")
        self.assertEqual(report["run_status"], "NOT_RUN")
        for name in ("type_bound_probes", "type_bound_mismatches", "node_count_mismatches", "calibration_checks", "calibration_node_mismatches"):
            self.assertIsNone(report["metrics"][name])


if __name__ == "__main__":
    unittest.main()

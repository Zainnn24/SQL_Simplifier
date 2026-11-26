"""Exercise driver bookkeeping with explicitly scripted test doubles only."""

from contextlib import redirect_stdout
import copy
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from evaluation.production import ProductionUnavailable
from evaluation.verifier_check import (
    load_fixtures, main, replay_witness, run_offline, run_production, validate_fixture,
)


def boundary_case(case_id="test-boundary"):
    return {
        "case_id": case_id,
        "schema_sql": "CREATE TABLE t (row_id BIGINT NOT NULL, x BIGINT NOT NULL)",
        "original_sql": "SELECT row_id FROM t WHERE x >= 0",
        "candidate_sql": "SELECT row_id FROM t WHERE x > 0",
        "expected_status": "DIFFERENT",
        "justification": "The legal boundary row x=0 is kept only by the inclusive original.",
        "counterexample": {"row": {"row_id": 1, "x": 0}, "original_truth": True,
                           "candidate_truth": False, "original_rows": [[1]], "candidate_rows": []},
        "enumeration": {"domains": {"x": [-1, 0, 1]}, "fixed_values": {"row_id": 1}},
    }


def scripted_result(status, witness=None):
    return {"status": status, "original_nodes": 3, "candidate_nodes": 3,
            "diagnostic": None if status in ("EQUIVALENT", "DIFFERENT") else "Scripted test outcome",
            "witness": witness}


def scripted_api(results):
    """Return fixed verdicts to test metrics, never a SQL-to-Z3 translator."""
    return SimpleNamespace(
        validate_input=Mock(return_value={"status": "READY", "original_nodes": 3, "diagnostic": None}),
        verify=Mock(side_effect=results),
    )


def scripted_boundary_observation(schema, query, row, *, timeout_ms):
    """Hand-written observations for the single test-boundary example."""
    truth = row["x"] > 0 if query.endswith("x > 0") else row["x"] >= 0
    return {"status": "EVALUATED", "schema_holds": True, "predicate_truth": truth, "diagnostic": None}


class VerifierCheckTests(unittest.TestCase):
    def test_loader_rejects_duplicate_ids_and_missing_fields(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            case = boundary_case()
            path.write_text(json.dumps(case) + "\n" + json.dumps(case), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate case_id"):
                load_fixtures(path)
            del case["justification"]
            path.write_text(json.dumps(case), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "eight shared-contract fields"):
                load_fixtures(path)

    def test_loader_rejects_boolean_counterexample_data(self):
        case = boundary_case()
        case["counterexample"]["row"]["x"] = False
        with self.assertRaisesRegex(ValueError, "integer data"):
            validate_fixture(case)

    def test_independent_replay_accepts_another_legal_distinguishing_row(self):
        case = boundary_case()
        witness = copy.deepcopy(case["counterexample"])
        witness["row"]["row_id"] = 44
        witness["original_rows"] = [[44]]
        result = replay_witness(case, witness)
        self.assertTrue(result["confirmed"], result)
        self.assertNotEqual(witness["row"], case["counterexample"]["row"])

    def test_independent_replay_rejects_incorrect_truth_and_illegal_rows(self):
        case = boundary_case()
        wrong = copy.deepcopy(case["counterexample"])
        wrong["original_truth"], wrong["candidate_truth"] = False, True
        self.assertFalse(replay_witness(case, wrong)["confirmed"])
        case["schema_sql"] = "CREATE TABLE t (row_id BIGINT NOT NULL, x BIGINT NOT NULL, CHECK (x > 0))"
        self.assertFalse(replay_witness(case, case["counterexample"])["confirmed"])

    def test_failed_witness_replay_preserves_infrastructure_observations(self):
        failure = {"status": "INFRASTRUCTURE_ERROR", "schema_holds": None,
                   "predicate_truth": None, "rows": None, "diagnostic": "insert: storage unavailable"}
        case = boundary_case()
        with patch("evaluation.verifier_check.observe_row", return_value=failure):
            replay = replay_witness(case, case["counterexample"])
        self.assertFalse(replay["confirmed"])
        self.assertEqual(replay["original"], failure)
        self.assertEqual(replay["candidate"]["diagnostic"], "insert: storage unavailable")
        self.assertEqual(replay["schema_sql"], case["schema_sql"])

    def test_offline_equivalence_cannot_pass_on_an_empty_schema(self):
        case = boundary_case()
        case.update(expected_status="EQUIVALENT", counterexample=None)
        case["schema_sql"] = "CREATE TABLE t (row_id BIGINT NOT NULL, x BIGINT NOT NULL, CHECK (x < 0), CHECK (x >= 0))"
        offline = run_offline([case])
        self.assertEqual(offline["summary"]["failed_cases"], 1)
        self.assertEqual(offline["cases"][0]["bounded"]["status"], "UNSAT_SCHEMA")

    def test_scripted_metrics_confirm_full_and_bounded_witnesses(self):
        case = boundary_case()
        api = scripted_api([scripted_result("DIFFERENT", case["counterexample"])] * 2)
        offline = run_offline([case])
        with patch("evaluation.verifier_check.evaluate_encoded_row", side_effect=scripted_boundary_observation):
            report = run_production([case], offline, api, 5000)
        self.assertEqual(report["discrepancies"], [])
        metrics = report["metrics"]
        self.assertEqual((metrics["full_checks"], metrics["bounded_checks"], metrics["row_checks"]), (1, 1, 6))
        self.assertEqual((metrics["sat_disagreement_models"], metrics["confirmed_replays"]), (2, 2))
        self.assertEqual(metrics["confirmed_replay_rate"], 1)
        self.assertEqual(metrics["row_mismatches"], 0)

    def test_replay_error_remains_in_all_sat_models_denominator(self):
        case = boundary_case()
        api = scripted_api([scripted_result("REPLAY_ERROR"), scripted_result("DIFFERENT", case["counterexample"])])
        with patch("evaluation.verifier_check.evaluate_encoded_row", side_effect=scripted_boundary_observation):
            report = run_production([case], run_offline([case]), api, 5000)
        metrics = report["metrics"]
        self.assertEqual(metrics["replay_errors"], 1)
        self.assertEqual((metrics["sat_disagreement_models"], metrics["confirmed_replays"]), (2, 1))
        self.assertEqual(metrics["confirmed_replay_rate"], 0.5)
        self.assertTrue(report["discrepancies"])

    def test_both_wrong_verdict_directions_are_counted_separately(self):
        different = boundary_case("different")
        equivalent = boundary_case("equivalent")
        equivalent.update(candidate_sql=equivalent["original_sql"], expected_status="EQUIVALENT", counterexample=None)
        results = [scripted_result("EQUIVALENT"), scripted_result("DIFFERENT", different["counterexample"]),
                   scripted_result("DIFFERENT", different["counterexample"]), scripted_result("EQUIVALENT")]
        with patch("evaluation.verifier_check.evaluate_encoded_row", side_effect=scripted_boundary_observation):
            report = run_production([different, equivalent], run_offline([different, equivalent]), scripted_api(results), 5000)
        metrics = report["metrics"]
        self.assertEqual(metrics["false_equivalent"], 1)
        self.assertEqual(metrics["false_different"], 1)
        self.assertEqual(metrics["wrong_directions_by_stage"]["full_range"], {"false_equivalent": 1, "false_different": 1})

    def test_bounded_comparison_uses_its_own_verdict_not_global_label(self):
        case = boundary_case("bounded-only")
        case.update(original_sql="SELECT row_id FROM t WHERE x <= 2", candidate_sql="SELECT row_id FROM t WHERE TRUE")
        case["counterexample"] = {"row": {"row_id": 1, "x": 3}, "original_truth": False,
                                  "candidate_truth": True, "original_rows": [], "candidate_rows": [[1]]}
        case["enumeration"]["domains"]["x"] = [-2, -1, 0, 1, 2]
        offline = run_offline([case])
        api = scripted_api([scripted_result("DIFFERENT", case["counterexample"]), scripted_result("EQUIVALENT")])
        observation = {"status": "EVALUATED", "schema_holds": True, "predicate_truth": True, "diagnostic": None}
        with patch("evaluation.verifier_check.evaluate_encoded_row", return_value=observation):
            report = run_production([case], offline, api, 5000)
        self.assertEqual(report["discrepancies"], [])
        self.assertEqual(report["cases"][0]["bounded"]["expected_status"], "EQUIVALENT")
        bounded_request = api.verify.call_args_list[1].kwargs
        self.assertNotEqual(bounded_request["schema_sql"], case["schema_sql"])
        self.assertEqual(case["expected_status"], "DIFFERENT")

    def test_row_translation_mismatches_preserve_reproducing_inputs(self):
        case = boundary_case()
        api = scripted_api([scripted_result("DIFFERENT", case["counterexample"])] * 2)
        wrong = {"status": "EVALUATED", "schema_holds": False, "predicate_truth": False, "diagnostic": None}
        with patch("evaluation.verifier_check.evaluate_encoded_row", return_value=wrong):
            report = run_production([case], run_offline([case]), api, 5000)
        self.assertEqual(report["metrics"]["row_mismatches"], 6)
        mismatch = next(item for item in report["discrepancies"] if item["stage"] == "encoded_row")
        self.assertIn("row", mismatch["inputs"])
        self.assertEqual(mismatch["inputs"]["schema_sql"], case["schema_sql"])

    def test_inconclusives_and_row_errors_are_not_successes(self):
        case = boundary_case()
        api = scripted_api([scripted_result("INCONCLUSIVE")] * 2)
        api.validate_input.return_value = {"status": "INCONCLUSIVE", "original_nodes": None, "diagnostic": "Scripted timeout"}
        row_error = {"status": "INCONCLUSIVE", "schema_holds": None, "predicate_truth": None, "diagnostic": "Scripted row timeout"}
        with patch("evaluation.verifier_check.evaluate_encoded_row", return_value=row_error):
            report = run_production([case], run_offline([case]), api, 5000)
        self.assertEqual(report["metrics"]["inconclusive_checks"], 2)
        self.assertEqual(report["metrics"]["validation_inconclusive"], 1)
        self.assertEqual(report["metrics"]["row_inconclusive"], 6)
        self.assertIsNone(report["metrics"]["confirmed_replay_rate"])

    def test_malformed_production_results_and_row_exceptions_are_recorded(self):
        case = boundary_case()
        api = scripted_api([{"status": "DIFFERENT"}, scripted_result("DIFFERENT", case["counterexample"])])
        with patch("evaluation.verifier_check.evaluate_encoded_row", side_effect=RuntimeError("Scripted row failure")):
            report = run_production([case], run_offline([case]), api, 5000)
        self.assertEqual(report["metrics"]["row_errors"], 6)
        self.assertEqual(report["metrics"]["sat_disagreement_models"], 2)
        self.assertEqual(report["metrics"]["confirmed_replays"], 1)
        self.assertTrue(report["discrepancies"])

    def test_validation_result_contract_rejects_missing_and_boolean_node_counts(self):
        case = boundary_case()
        for validation in ({"status": "READY"},
                           {"status": "READY", "original_nodes": True, "diagnostic": None},
                           {"status": "READY", "original_nodes": None, "diagnostic": None}):
            with self.subTest(validation=validation):
                api = scripted_api([scripted_result("DIFFERENT", case["counterexample"])] * 2)
                api.validate_input.return_value = validation
                with patch("evaluation.verifier_check.evaluate_encoded_row", side_effect=scripted_boundary_observation):
                    report = run_production([case], run_offline([case]), api, 5000)
                self.assertTrue(any(item["stage"] == "validation_result_contract" for item in report["discrepancies"]))
                self.assertGreater(report["metrics"]["infrastructure_errors"], 0)

    def test_fixtures_only_cli_never_loads_production_and_saves_na_metrics(self):
        with TemporaryDirectory() as directory:
            fixtures, output = Path(directory) / "cases.jsonl", Path(directory) / "report.json"
            fixtures.write_text(json.dumps(boundary_case()) + "\n", encoding="utf-8")
            with patch("evaluation.verifier_check.load_production", side_effect=AssertionError("Must not load production")), redirect_stdout(StringIO()):
                status = main(["--fixtures", str(fixtures), "--fixtures-only", "--output", str(output)])
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(status, 0)
        self.assertEqual(report["production"]["run_status"], "NOT_RUN")
        self.assertIsNone(report["production"]["metrics"]["false_equivalent"])
        self.assertEqual(report["offline"]["summary"]["confirmed_independent_replays"], 1)
        self.assertIn("fixture_sha256", report)

    def test_formal_cli_reports_missing_interfaces_and_nonzero_exit(self):
        missing = ("validate_input", "verify", "encode_query", "InputError")
        with TemporaryDirectory() as directory:
            fixtures, output = Path(directory) / "cases.jsonl", Path(directory) / "report.json"
            fixtures.write_text(json.dumps(boundary_case()) + "\n", encoding="utf-8")
            with patch("evaluation.verifier_check.load_production", side_effect=ProductionUnavailable("Scripted missing interfaces", missing=missing)), redirect_stdout(StringIO()):
                status = main(["--fixtures", str(fixtures), "--output", str(output)])
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(status, 2)
        self.assertEqual(report["production"]["missing_interfaces"], list(missing))
        self.assertEqual(report["production"]["run_status"], "NOT_RUN")
        self.assertIsNone(report["production"]["metrics"]["confirmed_replay_rate"])


if __name__ == "__main__":
    unittest.main()

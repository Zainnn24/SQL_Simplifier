"""Additional offline log-integrity cases; all mutations use temporary copies."""

import copy
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from evaluation.scorer import main, score_logs


EXAMPLE = Path(__file__).resolve().parents[1] / "scoring_examples/README_E2"


class ScorerEdgeCaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "batch"
        shutil.copytree(EXAMPLE, self.root)
        self.trace = self.root / "logs/trace.jsonl"
        self.records = [json.loads(line) for line in self.trace.read_text(encoding="utf-8").splitlines() if line.strip()]

    def save(self):
        self.trace.write_text("".join(json.dumps(record) + "\n" for record in self.records), encoding="utf-8")

    def score(self, configs=None):
        return score_logs(self.root / "manifest.jsonl", self.root / "logs",
                          config_paths=configs or [self.root / "config.json"], split="dev", repeats=(1,))

    def test_exact_original_copy_cannot_claim_fewer_nodes(self):
        original = json.loads((self.root / "manifest.jsonl").read_text())["original_sql"]
        self.records[1]["candidate_sql"] = self.records[1]["raw_response"] = original
        self.records[2]["selected_sql"] = original
        self.save()
        report = self.score()
        self.assertFalse(report["integrity"]["complete"])
        self.assertEqual(report["repetitions"][0]["final_simplification"]["numerator"], 0)

    def test_missing_end_does_not_give_a_falsely_shrinking_copy_first_answer_credit(self):
        original = json.loads((self.root / "manifest.jsonl").read_text())["original_sql"]
        self.records = [copy.deepcopy(self.records[1])]
        self.records[0].update(attempt_no=1, feedback=None, raw_response=original, candidate_sql=original)
        self.save()
        report = self.score()
        self.assertFalse(report["integrity"]["complete"])
        self.assertEqual(report["repetitions"][0]["first_simplification"]["numerator"], 0)

    def test_successful_check_original_count_must_match_preflight(self):
        self.records[1]["check_result"]["original_nodes"] = 8
        self.save()
        self.assertFalse(self.score()["integrity"]["complete"])

    def test_conflicting_files_with_the_same_config_id_are_rejected(self):
        second = json.loads((self.root / "config.json").read_text())
        second["model_id"] = "another-scripted-test-identity"
        second_path = self.root / "conflicting-config.json"
        second_path.write_text(json.dumps(second), encoding="utf-8")
        report = self.score([self.root / "config.json", second_path])
        self.assertFalse(report["integrity"]["complete"])

    def test_conflicting_manifest_definition_keeps_its_slot_but_cannot_earn_credit(self):
        manifest = self.root / "manifest.jsonl"
        conflicting = json.loads(manifest.read_text())
        conflicting["original_sql"] = "SELECT row_id FROM jobs WHERE FALSE"
        manifest.write_text(manifest.read_text() + json.dumps(conflicting) + "\n", encoding="utf-8")
        report = self.score()
        self.assertFalse(report["integrity"]["complete"])
        self.assertEqual(report["repetitions"][0]["scheduled_runs"], 1)
        self.assertEqual(report["repetitions"][0]["final_simplification"]["numerator"], 0)
        self.assertFalse(report["runs"][0]["integrity_valid"])

    def test_unknown_journal_record_cannot_make_a_complete_result(self):
        extra = {key: self.records[0][key] for key in ("run_id", "task_id", "split", "repeat", "config_id")}
        extra["record_type"] = "unreconciled_dispatch"
        self.records.append(extra)
        self.save()
        self.assertFalse(self.score()["integrity"]["complete"])

    def test_nonfinite_elapsed_time_is_corrupt_data(self):
        self.records[-1]["elapsed_ms"] = float("nan")
        self.save()
        self.assertFalse(self.score()["integrity"]["complete"])

    def test_extremely_large_elapsed_time_is_reported_without_overflow(self):
        self.records[-1]["elapsed_ms"] = 10**400
        self.save()
        report = self.score()
        self.assertFalse(report["integrity"]["complete"])
        json.dumps(report, allow_nan=False)

    def test_run_elapsed_cannot_be_less_than_its_sequential_attempts(self):
        self.records[-1]["elapsed_ms"] = 0
        self.save()
        self.assertFalse(self.score()["integrity"]["complete"])

    def test_different_witness_needs_distinct_outputs_consistent_with_truth(self):
        self.records[0]["check_result"]["witness"]["candidate_rows"] = []
        self.save()
        report = self.score()
        self.assertFalse(report["integrity"]["complete"])
        self.assertEqual(report["repetitions"][0]["recovery"]["denominator"], 0)

    def test_boolean_token_count_is_not_an_integer_zero(self):
        self.records[0]["input_tokens"] = False
        self.save()
        self.assertFalse(self.score()["integrity"]["complete"])

    def test_scripted_and_model_metadata_are_never_pooled(self):
        model_config = json.loads((self.root / "config.json").read_text())
        model_config.update(config_id="test-model-metadata-only", run_kind="model", model_id="no-model-was-called")
        model_path = self.root / "model-metadata.json"
        model_path.write_text(json.dumps(model_config), encoding="utf-8")
        copied = copy.deepcopy(self.records)
        for record in copied:
            record["config_id"] = model_config["config_id"]
            record["run_id"] = "model-metadata-only-r1"
        self.records.extend(copied)
        self.save()
        report = self.score([self.root / "config.json", model_path])
        self.assertTrue(report["integrity"]["complete"], report["integrity"])
        self.assertEqual({group["run_kind"] for group in report["repetitions"]}, {"scripted", "model"})
        self.assertEqual([group["scheduled_runs"] for group in report["repetitions"]], [1, 1])

    def test_partial_usage_retains_known_tokens_without_claiming_complete_accounting(self):
        self.records[0]["input_tokens"] = None
        self.records[1]["input_tokens"] = 5
        self.records[1]["output_tokens"] = 7
        self.records[2].update(input_tokens=5, output_tokens=7, token_accounting_complete=False)
        self.save()
        report = self.score()
        self.assertTrue(report["integrity"]["complete"], report["integrity"])
        resources = report["repetitions"][0]["resources"]
        self.assertEqual(resources["input_tokens_known"], 5)
        self.assertEqual(resources["output_tokens_known"], 7)
        self.assertFalse(resources["token_accounting_complete"])

    def test_attempt_one_is_defined_by_number_not_record_order(self):
        self.records = [self.records[1], self.records[0], self.records[2]]
        self.save()
        report = self.score()
        self.assertEqual(report["repetitions"][0]["first_equivalence"]["numerator"], 0)
        self.assertEqual(report["repetitions"][0]["first_simplification"]["numerator"], 0)

    def test_a_stop_verdict_cannot_be_followed_by_a_successful_retry(self):
        self.records[0]["check_result"].update(status="INCONCLUSIVE", candidate_nodes=None,
                                                diagnostic="Scripted solver timeout", witness=None)
        self.save()
        report = self.score()
        self.assertFalse(report["integrity"]["complete"])
        self.assertEqual(report["repetitions"][0]["final_simplification"]["numerator"], 0)

    def test_no_attempt_may_follow_an_already_acceptable_candidate(self):
        self.records[0]["candidate_sql"] = self.records[1]["candidate_sql"]
        self.records[0]["raw_response"] = self.records[1]["raw_response"]
        self.records[0]["check_result"] = copy.deepcopy(self.records[1]["check_result"])
        self.save()
        report = self.score()
        self.assertFalse(report["integrity"]["complete"])
        self.assertEqual(report["repetitions"][0]["final_simplification"]["numerator"], 0)

    def cli_status(self, *extra):
        args = ["--manifest", str(self.root / "manifest.jsonl"), "--logs", str(self.root / "logs"),
                "--config", str(self.root / "config.json"), "--split", "dev", "--repeats", "1", *extra]
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            try:
                return main(args)
            except SystemExit as error:
                return error.code

    def test_cli_cannot_overwrite_an_input_log_with_output(self):
        before = self.trace.read_bytes()
        self.assertNotEqual(self.cli_status("--output", str(self.trace)), 0)
        self.assertEqual(self.trace.read_bytes(), before)

    def test_cli_cannot_create_jsonl_output_inside_its_input_tree(self):
        output = self.root / "logs/generated-result.jsonl"
        self.assertNotEqual(self.cli_status("--output", str(output)), 0)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()

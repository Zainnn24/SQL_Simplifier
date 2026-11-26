"""Closeout regressions use temporary scripted packets and never model calls."""

from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from evaluation.scorer import score_logs


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "evaluation/scoring_examples/README_E2"


class ScorerCloseoutTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.packet = Path(temporary.name) / "packet"
        shutil.copytree(EXAMPLE, self.packet)
        self.manifest = self.packet / "manifest.jsonl"
        self.config = self.packet / "config.json"
        self.logs = self.packet / "logs"
        self.trace = self.logs / "trace.jsonl"
        self.task = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.records = [json.loads(line) for line in self.trace.read_text(encoding="utf-8").splitlines() if line.strip()]

    def save(self):
        self.trace.write_text("".join(json.dumps(record) + "\n" for record in self.records), encoding="utf-8", newline="\n")

    def score(self):
        return score_logs(self.manifest, self.logs, config_paths=[self.config], split="dev", repeats=(1,))

    def groups(self, report):
        return report["repetitions"] + report["family_groups"] + report["category_groups"]

    def contradictory_copy(self):
        first = self.records[0]
        first["raw_response"] = first["candidate_sql"] = self.task["original_sql"]
        first["check_result"]["original_nodes"] = 7
        first["check_result"]["candidate_nodes"] = 7
        self.save()

    def cli(self):
        output, summary = self.packet / "closeout-report.json", self.packet / "closeout-summary.txt"
        result = subprocess.run(
            [sys.executable, "-B", "-m", "evaluation", "score",
             "--manifest", str(self.manifest), "--logs", str(self.logs),
             "--config", str(self.config), "--split", "dev", "--repeats", "1",
             "--output", str(output), "--summary", str(summary)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        return result, json.loads(output.read_text(encoding="utf-8")), output, summary

    def assert_no_recovery_or_final_credit(self, report):
        self.assertFalse(report["integrity"]["complete"])
        self.assertFalse(report["runs"][0]["first_different"])
        for group in self.groups(report):
            self.assertEqual(group["recovery"], {"numerator": 0, "denominator": 0, "rate": None})
            self.assertEqual(group["final_simplification"]["numerator"], 0)

    def test_identical_copy_cannot_claim_different_even_with_equal_node_counts(self):
        self.contradictory_copy()
        report = self.score()
        self.assert_no_recovery_or_final_credit(report)
        issue = next(item for item in report["integrity"]["issues"] if item["code"] == "COPY_VERDICT_CONTRADICTION")
        self.assertEqual(issue["run_id"], self.records[0]["run_id"])
        self.assertEqual(issue["task_id"], self.task["task_id"])
        self.assertEqual((issue["attempt_no"], issue["file"], issue["line"]), (1, "trace.jsonl", 1))

    def test_identical_different_copy_is_rejected_without_a_run_end(self):
        self.contradictory_copy()
        self.records = self.records[:1]
        self.save()
        report = self.score()
        self.assert_no_recovery_or_final_credit(report)
        codes = {issue["code"] for issue in report["integrity"]["issues"]}
        self.assertTrue({"COPY_VERDICT_CONTRADICTION", "MISSING_RUN_END"}.issubset(codes))

    def test_honest_equivalent_copy_keeps_first_equivalence_credit(self):
        self.contradictory_copy()
        self.records[0]["check_result"].update(status="EQUIVALENT", witness=None)
        self.save()
        report = self.score()
        self.assertTrue(report["integrity"]["complete"], report["integrity"])
        for group in self.groups(report):
            self.assertEqual(group["first_equivalence"]["numerator"], 1)
            self.assertEqual(group["first_simplification"]["numerator"], 0)
            self.assertEqual(group["final_simplification"]["numerator"], 1)
            self.assertIsNone(group["recovery"]["rate"])

    def test_cli_rejects_copy_contradiction_and_writes_lf_artifacts(self):
        self.contradictory_copy()
        result, report, output, summary = self.cli()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assert_no_recovery_or_final_credit(report)
        self.assertIn("Log integrity: INCOMPLETE", result.stdout)
        self.assertNotIn(b"\r\n", output.read_bytes())
        self.assertNotIn(b"\r\n", summary.read_bytes())

    def test_wrong_split_does_not_fill_the_planned_dev_slot(self):
        for record in self.records:
            record["split"] = "test"
        self.save()
        report = self.score()
        self.assertFalse(report["integrity"]["complete"])
        missing = [issue for issue in report["integrity"]["issues"] if issue["code"] == "MISSING_SLOT"]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["split"], "dev")
        for group in self.groups(report):
            self.assertEqual((group["scheduled_runs"], group["observed_runs"], group["missing_runs"]), (1, 0, 1))
            self.assertEqual(group["final_simplification"], {"numerator": 0, "denominator": 1, "rate": 0.0})
            self.assertEqual(group["resources"]["calls"], 0)
            self.assertFalse(group["resources"]["calls_accounting_complete"])
            self.assertFalse(group["resources"]["token_accounting_complete"])
            self.assertEqual(group["resources"]["elapsed_unknown_runs"], 1)

    def test_legal_dev_run_keeps_credit_when_an_extra_test_run_is_present(self):
        legal_run_id = self.records[0]["run_id"]
        extra = deepcopy(self.records)
        for record in extra:
            record.update(split="test", run_id="unexpected-test-run")
        self.records.extend(extra)
        self.save()
        report = self.score()
        self.assertFalse(report["integrity"]["complete"])
        self.assertNotIn("SLOT_COLLISION", {issue["code"] for issue in report["integrity"]["issues"]})
        self.assertTrue(next(run for run in report["runs"] if run["run_id"] == legal_run_id)["integrity_valid"])
        for group in self.groups(report):
            self.assertEqual((group["scheduled_runs"], group["observed_runs"], group["missing_runs"]), (1, 1, 0))
            self.assertEqual(group["final_simplification"]["numerator"], 1)
            self.assertEqual(group["recovery"], {"numerator": 1, "denominator": 1, "rate": 1.0})
            self.assertEqual(group["resources"]["calls"], 2)

    def test_collisions_in_an_extra_split_do_not_invalidate_the_dev_run(self):
        legal_run_id = self.records[0]["run_id"]
        for name in ("unexpected-test-one", "unexpected-test-two"):
            extra = deepcopy(self.records[:3])
            for record in extra:
                record.update(split="test", run_id=name)
            self.records.extend(extra)
        self.save()
        report = self.score()
        collisions = [issue for issue in report["integrity"]["issues"] if issue["code"] == "SLOT_COLLISION"]
        self.assertEqual(len(collisions), 2)
        self.assertEqual({issue["split"] for issue in collisions}, {"test"})
        self.assertNotIn(legal_run_id, {issue["run_id"] for issue in collisions})
        self.assertEqual(report["repetitions"][0]["final_simplification"]["numerator"], 1)

    def test_cli_marks_a_wrong_split_only_batch_missing_and_incomplete(self):
        for record in self.records:
            record["split"] = "test"
        self.save()
        result, report, _, _ = self.cli()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(report["integrity"]["complete"])
        self.assertEqual(report["repetitions"][0]["missing_runs"], 1)
        self.assertIn("scheduled=1", result.stdout)


if __name__ == "__main__":
    unittest.main()

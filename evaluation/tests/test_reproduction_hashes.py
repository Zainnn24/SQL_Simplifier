"""Saved reports hash the actual LF input bytes, independent of Git autocrlf."""

import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ReproductionHashTests(unittest.TestCase):
    def test_saved_scoring_reports_match_all_input_bytes(self):
        for batch in ("README_E2", "EVALUATION_COPY"):
            root = ROOT / "evaluation/scoring_examples" / batch
            report = json.loads((root / "result.json").read_text(encoding="utf-8"))
            for group, relative in (("manifest", "manifest.jsonl"), ("configs", "config.json"), ("logs", "logs/trace.jsonl")):
                with self.subTest(batch=batch, source=relative):
                    data = (root / relative).read_bytes()
                    self.assertNotIn(b"\r", data)
                    self.assertEqual(hashlib.sha256(data).hexdigest(), report["sources"][group][0]["sha256"])

    def test_experiment_a_reports_match_actual_fixture_bytes(self):
        data = (ROOT / "evaluation/verifier_cases.jsonl").read_bytes()
        self.assertNotIn(b"\r", data)
        for filename in ("experiment_a_offline.json", "experiment_a_production.json"):
            with self.subTest(report=filename):
                report = json.loads((ROOT / "evaluation/results" / filename).read_text(encoding="utf-8"))
                self.assertEqual(report["fixture_sha256"], hashlib.sha256(data).hexdigest())

    def test_benchmark_receipt_reports_match_all_saved_input_bytes(self):
        sources = {
            "manifest": ROOT / "evaluation/manifests/martin_draft.jsonl",
            "reviews": ROOT / "evaluation/benchmark_reviews.jsonl",
            "seed_file": ROOT / "data/SQL_Predicate_Family_Examples.sql",
            "experiment_a_report": ROOT / "evaluation/results/experiment_a_production.json",
        }
        for filename in ("benchmark_draft.json", "benchmark_formal.json"):
            report = json.loads((ROOT / "evaluation/results" / filename).read_text(encoding="utf-8"))
            for key, path in sources.items():
                with self.subTest(report=filename, source=key):
                    if key == "experiment_a_report" and filename == "benchmark_draft.json":
                        self.assertIsNone(report["input_sha256"][key])
                        continue
                    data = path.read_bytes()
                    self.assertNotIn(b"\r", data)
                    self.assertEqual(report["input_sha256"][key], hashlib.sha256(data).hexdigest())


if __name__ == "__main__":
    unittest.main()

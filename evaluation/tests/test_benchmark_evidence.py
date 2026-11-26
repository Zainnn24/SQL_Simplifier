"""Reject deliberately synthetic evidence controls; none are real Experiment A.

All fabricated report objects live in temporary directories and must fail. The
tests neither generate formal acceptance nor replace the production verifier.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from evaluation.benchmark_check import ScreeningIncomplete, _evidence, _structure, check_removal
from evaluation.sample_check import ROOT
from evaluation.verifier_check import dependency_versions, load_fixtures


def e2_control():
    """An isolated CHECK-removal test shape, not another benchmark contribution."""
    task = {
        "schema_sql": "CREATE TABLE jobs (row_id BIGINT NOT NULL, start_day BIGINT NOT NULL, end_day BIGINT NOT NULL, CHECK (start_day <= end_day), CHECK (row_id >= 0))",
        "original_sql": "SELECT row_id FROM jobs WHERE start_day >= 10 AND end_day >= 10",
        "reference_sql": "SELECT row_id FROM jobs WHERE start_day >= 10",
    }
    removal = {
        "removed_check_sql": "CHECK (start_day <= end_day)",
        "schema_without_check_sql": "CREATE TABLE jobs (row_id BIGINT NOT NULL, start_day BIGINT NOT NULL, end_day BIGINT NOT NULL, CHECK (row_id >= 0))",
        "row": {"row_id": 1, "start_day": 10, "end_day": 9},
        "original_truth": False, "reference_truth": True,
        "original_rows": [], "reference_rows": [[1]],
    }
    return task, removal


def invalid_summary_only_report(*, tiny):
    """Return intentionally false claims used only to test report rejection."""
    fixture = ROOT / "evaluation/verifier_cases.jsonl"
    cases = load_fixtures(fixture)
    from math import prod
    rows = sum(prod(len(domain) for domain in case["enumeration"]["domains"].values()) for case in cases if case["enumeration"])
    bounded = sum(case["enumeration"] is not None for case in cases)
    replays = sum(case["expected_status"] == "DIFFERENT" for case in cases)
    if tiny:
        rows = bounded = replays = 1
    provenance = {"verified": True, "fingerprint": "explicit-synthetic-negative-control"}
    summary = {"fixtures": len(cases), "failed_cases": 0, "type_bound_probes": 8,
               "type_bound_probe_errors": 0, "enumerated_rows": rows, "bounded_cases": bounded,
               "independent_replays": replays, "confirmed_independent_replays": replays}
    metrics = {name: 0 for name in (
        "false_equivalent", "false_different", "verdict_mismatches", "validation_mismatches", "validation_inconclusive", "inconclusive_checks",
        "row_mismatches", "row_inconclusive", "row_errors", "replay_errors", "infrastructure_errors", "node_count_mismatches",
        "type_bound_mismatches", "type_bound_errors", "type_bound_inconclusive", "calibration_node_mismatches", "calibration_errors",
    )}
    metrics.update(full_checks=len(cases), bounded_checks=bounded, row_checks=2 * rows,
                   type_bound_probes=8, calibration_cases=3, calibration_checks=3, calibration_validation_checks=3,
                   sat_disagreement_models=1, confirmed_replays=1, confirmed_replay_rate=1,
                   calibration_sat_disagreement_models=1, calibration_confirmed_replays=1, calibration_confirmed_replay_rate=1)
    report = {"test_control": "Synthetic corrupted evidence; must never be accepted as production",
              "mode": "production", "exit_code": 0, "test_standin": False,
              "dependency_versions": dependency_versions(), "verifier_provenance": provenance,
              "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
              "offline": {"summary": summary},
              "production": {"run_status": "COMPLETED", "discrepancies": [], "metrics": metrics}}
    return report, provenance


class BenchmarkEvidenceRegressionTests(unittest.TestCase):
    def assert_summary_rejected(self, tiny):
        report, provenance = invalid_summary_only_report(tiny=tiny)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic-invalid-evidence.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaises(ValueError):
                _evidence(path, provenance, dependency_versions())

    def test_self_reported_tiny_coverage_cannot_replace_current_fixture_coverage(self):
        self.assert_summary_rejected(tiny=True)

    def test_matching_summary_counts_without_case_probe_and_calibration_details_are_rejected(self):
        self.assert_summary_rejected(tiny=False)

    def test_boolean_replay_counts_cannot_masquerade_as_integer_coverage(self):
        report, provenance = invalid_summary_only_report(tiny=False)
        report["production"]["metrics"]["confirmed_replays"] = True
        with TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic-boolean-count.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unconfirmed disagreement"):
                _evidence(path, provenance, dependency_versions())

    def test_column_definition_cannot_masquerade_as_the_removed_check_clause(self):
        task, removal = e2_control()
        self.assertTrue(check_removal(task, removal)["passed"])
        removal["removed_check_sql"] = "unrelated BIGINT NOT NULL CHECK (start_day <= end_day)"
        with self.assertRaisesRegex(ValueError, "not a column definition"):
            check_removal(task, removal)

    def test_named_check_selector_removes_only_its_exact_occurrence(self):
        task, removal = e2_control()
        task["schema_sql"] = task["schema_sql"].replace("CHECK (start_day <= end_day)", "CONSTRAINT ordered_days CHECK (start_day <= end_day)")
        removal["removed_check_sql"] = "CONSTRAINT ordered_days CHECK (start_day <= end_day)"
        self.assertTrue(check_removal(task, removal)["passed"])

    def test_renaming_seven_used_columns_cannot_silently_claim_a_clean_screen(self):
        names = "abcdefg"
        declarations = ", ".join(f"{name} BIGINT NOT NULL" for name in names)
        predicate = " AND ".join(f"({name} >= 0 OR {name} = 0)" for name in names)
        original = {"schema_sql": f"CREATE TABLE t ({declarations}, CHECK (a >= -10))",
                    "original_sql": f"SELECT a FROM t WHERE {predicate}"}
        renamed = deepcopy(original)
        renamed = {key: value.translate(str.maketrans({"a": "g", "g": "a"})) for key, value in renamed.items()}
        for task in (original, renamed):
            with self.subTest(sql=task["original_sql"]):
                with self.assertRaisesRegex(ScreeningIncomplete, "independent structural"):
                    _structure(task)


if __name__ == "__main__":
    unittest.main()

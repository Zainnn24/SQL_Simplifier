"""Score explicitly scripted fixtures and temporary scheduling substitutes.

No fixture in this module is a measured model run or an Alvin pipeline delivery.
Tests mutate temporary copies and never dispatch a model request.
"""

from copy import deepcopy
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from evaluation.scorer import score_logs


ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "evaluation/scoring_examples"


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path, records):
    path.write_text("".join(json.dumps(record, ensure_ascii=True) + "\n" for record in records), encoding="utf-8")


def readme_trace_bytes():
    # README follows the user's Git checkout policy; reproduction inputs use LF.
    source = (ROOT / "README.md").read_bytes().replace(b"\r\n", b"\n")
    section = source.index(b"#### E2 scripted JSONL trace")
    start = source.index(b"```jsonl", section) + len(b"```jsonl")
    start = source.index(b"\n", start) + 1
    end = source.index(b"```", start)
    return source[start:end]


class ScorerTests(unittest.TestCase):
    def sandbox(self, name="README_E2"):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name) / name
        shutil.copytree(EXAMPLES / name, directory)
        packet = SimpleNamespace(
            directory=directory,
            manifest=directory / "manifest.jsonl",
            config=directory / "config.json",
            logs=directory / "logs",
            trace=directory / "logs/trace.jsonl",
        )
        packet.tasks = read_jsonl(packet.manifest)
        packet.records = read_jsonl(packet.trace)
        packet.settings = json.loads(packet.config.read_text(encoding="utf-8"))
        return packet

    def save(self, packet):
        write_jsonl(packet.manifest, packet.tasks)
        write_jsonl(packet.trace, packet.records)
        packet.config.write_text(json.dumps(packet.settings, ensure_ascii=True) + "\n", encoding="utf-8")

    def score(self, packet, **kwargs):
        return score_logs(
            packet.manifest, packet.logs,
            config_paths=kwargs.pop("config_paths", [packet.config]),
            split=kwargs.pop("split", "dev"),
            repeats=kwargs.pop("repeats", (1,)),
            **kwargs,
        )

    def only_group(self, report):
        self.assertEqual(len(report["repetitions"]), 1)
        return report["repetitions"][0]

    def assert_metric(self, metric, numerator, denominator):
        self.assertEqual(metric["numerator"], numerator)
        self.assertEqual(metric["denominator"], denominator)
        if denominator:
            self.assertAlmostEqual(metric["rate"], numerator / denominator)
        else:
            self.assertIsNone(metric["rate"])

    def assert_incomplete_without_success(self, report):
        self.assertFalse(report["integrity"]["complete"])
        self.assertTrue(report["integrity"]["issues"])
        for group in report["repetitions"]:
            self.assertEqual(group["final_simplification"]["numerator"], 0)

    def failed_attempt_packet(self, outcome):
        packet = self.sandbox()
        attempt, end = deepcopy(packet.records[0]), deepcopy(packet.records[-1])
        attempt.update({
            "outcome": outcome, "raw_response": None, "candidate_sql": None,
            "check_result": None, "diagnostic": f"Synthetic test-only {outcome} outcome.",
            "elapsed_ms": 10,
        })
        end.update({
            "terminal_reason": outcome, "accepted": False, "selected_sql": None,
            "calls_used": 1, "elapsed_ms": 20,
            "diagnostic": f"Synthetic test-only {outcome} terminal record.",
        })
        packet.records = [attempt, end]
        self.save(packet)
        return packet

    def test_readme_trace_is_an_exact_three_line_source_copy(self):
        path = EXAMPLES / "README_E2/logs/trace.jsonl"
        self.assertEqual(path.read_bytes(), readme_trace_bytes())
        self.assertEqual(len(read_jsonl(path)), 3)
        config = json.loads((EXAMPLES / "README_E2/config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["config_id"], "e2-scripted-v1")
        self.assertEqual(config["run_kind"], "scripted")
        self.assertNotIn("RECORD_", json.dumps(config))

    def test_readme_e2_scores_match_the_independent_contract(self):
        packet = self.sandbox()
        report = self.score(packet)
        self.assertTrue(report["integrity"]["complete"], report["integrity"])
        group = self.only_group(report)
        self.assertEqual(group["scheduled_runs"], 1)
        self.assert_metric(group["first_equivalence"], 0, 1)
        self.assert_metric(group["first_simplification"], 0, 1)
        self.assert_metric(group["final_simplification"], 1, 1)
        self.assert_metric(group["recovery"], 1, 1)
        self.assertEqual(group["reductions"]["accepted_runs"], 1)
        self.assertEqual(group["reductions"]["nodes_removed_total"], 4)
        self.assertEqual(group["resources"]["calls"], 2)
        self.assertEqual(group["resources"]["elapsed_ms"], 70)

    def test_evaluation_copy_counts_equivalence_without_simplification_credit(self):
        packet = self.sandbox("EVALUATION_COPY")
        report = self.score(packet)
        self.assertTrue(report["integrity"]["complete"], report["integrity"])
        group = self.only_group(report)
        self.assert_metric(group["first_equivalence"], 1, 1)
        self.assert_metric(group["first_simplification"], 0, 1)
        self.assert_metric(group["final_simplification"], 0, 1)
        self.assert_metric(group["recovery"], 0, 0)
        self.assertEqual(group["resources"]["calls"], 4)
        self.assertEqual(group["resources"]["elapsed_ms"], 50)
        self.assertEqual(group["reductions"]["accepted_runs"], 0)
        self.assertEqual(group["reductions"]["nodes_removed_total"], 0)
        self.assertIn("BUDGET_EXHAUSTED", json.dumps(group["outcomes"]))

    def test_missing_scheduled_task_stays_in_the_manifest_denominator(self):
        packet = self.sandbox()
        extra = deepcopy(packet.tasks[0])
        extra["task_id"] = "E2-02"
        extra["source"] = "Synthetic test-only scheduling record, not a benchmark contribution."
        packet.tasks.append(extra)
        self.save(packet)
        report = self.score(packet)
        self.assertFalse(report["integrity"]["complete"])
        group = self.only_group(report)
        self.assertEqual(group["scheduled_runs"], 2)
        self.assert_metric(group["final_simplification"], 1, 2)
        self.assert_metric(group["first_equivalence"], 0, 2)

    def test_duplicate_attempt_or_terminal_record_cannot_earn_success(self):
        for source_index in (0, -1):
            with self.subTest(source_index=source_index):
                packet = self.sandbox()
                packet.records.append(deepcopy(packet.records[source_index]))
                self.save(packet)
                self.assert_incomplete_without_success(self.score(packet))

    def test_distinct_run_ids_for_one_scheduled_cell_are_not_double_counted(self):
        packet = self.sandbox()
        duplicates = deepcopy(packet.records)
        for record in duplicates:
            record["run_id"] = "synthetic-duplicate-run"
        packet.records.extend(duplicates)
        self.save(packet)
        report = self.score(packet)
        self.assert_incomplete_without_success(report)
        self.assertEqual(self.only_group(report)["scheduled_runs"], 1)

    def test_unknown_task_remains_visible_and_cannot_replace_a_scheduled_task(self):
        packet = self.sandbox()
        for record in packet.records:
            record["task_id"] = "UNKNOWN-01"
        self.save(packet)
        report = self.score(packet)
        self.assert_incomplete_without_success(report)
        self.assertIn("UNKNOWN-01", json.dumps(report))
        self.assertEqual(self.only_group(report)["scheduled_runs"], 1)

    def test_unregistered_config_and_inconsistent_identity_are_rejected(self):
        for field, value in (("config_id", "unregistered-config"), ("repeat", 2), ("split", "test")):
            with self.subTest(field=field):
                packet = self.sandbox()
                packet.records[1][field] = value
                self.save(packet)
                self.assert_incomplete_without_success(self.score(packet))

    def test_one_run_cannot_mix_two_registered_configurations(self):
        packet = self.sandbox()
        second = deepcopy(packet.settings)
        second["config_id"] = "synthetic-second-config"
        second_path = packet.directory / "second-config.json"
        second_path.write_text(json.dumps(second), encoding="utf-8")
        packet.records[1]["config_id"] = second["config_id"]
        self.save(packet)
        self.assert_incomplete_without_success(self.score(packet, config_paths=[packet.config, second_path]))

    def test_output_error_then_success_does_not_move_first_attempt_metrics(self):
        packet = self.sandbox()
        packet.records[0].update({
            "outcome": "OUTPUT_ERROR", "raw_response": "No SQL was extracted.",
            "candidate_sql": None, "check_result": None,
            "diagnostic": "Synthetic extraction failure before the successful second attempt.",
        })
        packet.records[1]["feedback"] = "The preceding response did not contain SQL; return one SELECT."
        self.save(packet)
        report = self.score(packet)
        self.assertTrue(report["integrity"]["complete"], report["integrity"])
        group = self.only_group(report)
        self.assert_metric(group["first_equivalence"], 0, 1)
        self.assert_metric(group["first_simplification"], 0, 1)
        self.assert_metric(group["final_simplification"], 1, 1)
        self.assert_metric(group["recovery"], 0, 0)
        self.assertIn("OUTPUT_ERROR", json.dumps(group["outcomes"]))

    def test_zero_call_preflight_terminal_is_distinct_from_a_missing_run(self):
        packet = self.sandbox()
        end = deepcopy(packet.records[-1])
        end.update({
            "preflight_result": {"status": "INCONCLUSIVE", "original_nodes": 7, "diagnostic": "Synthetic preflight timeout."},
            "terminal_reason": "INCONCLUSIVE", "accepted": False, "selected_sql": None,
            "calls_used": 0, "input_tokens": 0, "output_tokens": 0,
            "token_accounting_complete": True, "elapsed_ms": 5,
            "diagnostic": "Synthetic preflight timeout; no request dispatched.",
        })
        packet.records = [end]
        self.save(packet)
        represented = self.score(packet)
        self.assertTrue(represented["integrity"]["complete"], represented["integrity"])
        group = self.only_group(represented)
        self.assert_metric(group["final_simplification"], 0, 1)
        self.assertEqual(group["resources"]["calls"], 0)
        self.assertIn("INCONCLUSIVE", json.dumps(group["outcomes"]))
        packet.records = []
        self.save(packet)
        missing = self.score(packet)
        self.assertFalse(missing["integrity"]["complete"])
        self.assertTrue(missing["integrity"]["issues"])
        self.assertEqual(self.only_group(missing)["scheduled_runs"], 1)

    def test_api_error_and_incomplete_dispatches_remain_visible_failures(self):
        for outcome in ("API_ERROR", "INCOMPLETE"):
            with self.subTest(outcome=outcome):
                report = self.score(self.failed_attempt_packet(outcome))
                group = self.only_group(report)
                self.assert_metric(group["first_equivalence"], 0, 1)
                self.assert_metric(group["final_simplification"], 0, 1)
                self.assertEqual(group["resources"]["calls"], 1)
                self.assertIn(outcome, json.dumps(group["outcomes"]))

    def test_replay_error_is_visible_without_confirmed_error_recovery_credit(self):
        packet = self.sandbox()
        attempt, end = deepcopy(packet.records[0]), deepcopy(packet.records[-1])
        attempt["check_result"].update({
            "status": "REPLAY_ERROR", "diagnostic": "Synthetic disagreement could not be replayed.", "witness": None,
        })
        attempt["elapsed_ms"] = 10
        end.update({
            "terminal_reason": "REPLAY_ERROR", "accepted": False, "selected_sql": None,
            "calls_used": 1, "elapsed_ms": 20, "diagnostic": "Synthetic replay failure stops the run.",
        })
        packet.records = [attempt, end]
        self.save(packet)
        report = self.score(packet)
        group = self.only_group(report)
        self.assert_metric(group["final_simplification"], 0, 1)
        self.assert_metric(group["recovery"], 0, 0)
        self.assertIn("REPLAY_ERROR", json.dumps(group["outcomes"]))

    def test_partial_token_usage_preserves_known_totals_and_unknown_counts(self):
        packet = self.sandbox()
        # This temporary provider-log substitute is never a measured model result.
        packet.settings.update({"run_kind": "model", "model_id": "synthetic-test-resource-model"})
        packet.records[0].update({"input_tokens": None, "output_tokens": None})
        packet.records[1].update({"input_tokens": 5, "output_tokens": 7})
        packet.records[-1].update({"input_tokens": 5, "output_tokens": 7, "token_accounting_complete": False})
        self.save(packet)
        report = self.score(packet)
        resources = self.only_group(report)["resources"]
        self.assertEqual(resources["input_tokens_known"], 5)
        self.assertEqual(resources["output_tokens_known"], 7)
        self.assertEqual(resources["input_tokens_unknown_attempts"], 1)
        self.assertEqual(resources["output_tokens_unknown_attempts"], 1)
        self.assertFalse(resources["token_accounting_complete"])

    def test_all_unknown_tokens_are_not_reported_as_complete_zero_usage(self):
        packet = self.sandbox()
        packet.settings.update({"run_kind": "model", "model_id": "synthetic-test-resource-model"})
        for record in packet.records:
            record.update({"input_tokens": None, "output_tokens": None})
        packet.records[-1]["token_accounting_complete"] = False
        self.save(packet)
        report = self.score(packet)
        resources = self.only_group(report)["resources"]
        self.assertEqual(resources["input_tokens_unknown_attempts"], 2)
        self.assertEqual(resources["output_tokens_unknown_attempts"], 2)
        self.assertFalse(resources["token_accounting_complete"])

    def test_accepted_copy_or_mismatched_selected_sql_cannot_earn_credit(self):
        for fault in ("copy", "selected_sql"):
            with self.subTest(fault=fault):
                packet = self.sandbox()
                original = packet.tasks[0]["original_sql"]
                if fault == "copy":
                    packet.records[1].update({"candidate_sql": original, "raw_response": original})
                    packet.records[1]["check_result"]["candidate_nodes"] = 7
                packet.records[-1]["selected_sql"] = original
                self.save(packet)
                self.assert_incomplete_without_success(self.score(packet))

    def test_fifth_dispatched_request_violates_the_four_call_cap(self):
        packet = self.sandbox("EVALUATION_COPY")
        fifth = deepcopy(packet.records[-2])
        fifth["attempt_no"] = 5
        packet.records.insert(-1, fifth)
        packet.records[-1].update({"calls_used": 5, "elapsed_ms": 60})
        self.save(packet)
        self.assert_incomplete_without_success(self.score(packet))

    def test_repeated_scoring_is_identical_and_keeps_source_files_unchanged(self):
        packet = self.sandbox()
        paths = [packet.manifest, packet.config, packet.trace, packet.directory / "SOURCE.md"]
        before = {path: path.read_bytes() for path in paths}
        first = self.score(packet)
        second = self.score(packet)
        self.assertEqual(first, second)
        self.assertEqual(before, {path: path.read_bytes() for path in paths})

    def test_eighty_tasks_keep_eighty_per_repeat_with_or_without_terminal_records(self):
        packet = self.sandbox()
        # All records below are temporary scheduler substitutes, not benchmark tasks.
        development = {"A2", "B3", "C1", "D1", "E2"}
        families = [f"{category}{number}" for category in "ABCDE" for number in range(1, 6)
                    if f"{category}{number}" not in development]
        packet.tasks = []
        for family in families:
            for variant in range(1, 5):
                task = deepcopy(read_jsonl(EXAMPLES / "README_E2/manifest.jsonl")[0])
                task.update({
                    "task_id": f"{family}-{variant:02d}", "family": family, "split": "test",
                    "source": "Synthetic scheduler-only fixture; not measured model data or a benchmark contribution.",
                })
                packet.tasks.append(task)
        self.assertEqual(len(packet.tasks), 80)
        packet.settings.update({"config_id": "synthetic-schedule-v1", "model_id": "synthetic-no-model-calls",
                                "benchmark_version": "synthetic-schedule-only-v1"})
        packet.records = []
        self.save(packet)
        missing = self.score(packet, split="test", repeats=(1, 2, 3), expected_tasks_per_repeat=80)
        self.assertFalse(missing["integrity"]["complete"])
        for repeat in (1, 2, 3):
            for task in packet.tasks:
                packet.records.append({
                    "record_type": "run_end", "run_id": f"synthetic-{task['task_id']}-r{repeat}",
                    "task_id": task["task_id"], "split": "test", "repeat": repeat,
                    "config_id": packet.settings["config_id"],
                    "preflight_result": {"status": "INCONCLUSIVE", "original_nodes": 7,
                                         "diagnostic": "Synthetic scheduler preflight control; no provider called."},
                    "terminal_reason": "INCONCLUSIVE", "accepted": False, "selected_sql": None,
                    "calls_used": 0, "input_tokens": 0, "output_tokens": 0,
                    "token_accounting_complete": True, "elapsed_ms": 1,
                    "diagnostic": "Synthetic scheduler record only; no model was called.",
                })
        self.save(packet)
        represented = self.score(packet, split="test", repeats=(1, 2, 3), expected_tasks_per_repeat=80)
        self.assertTrue(represented["integrity"]["complete"], represented["integrity"])
        for report in (missing, represented):
            self.assertEqual(len(report["repetitions"]), 3)
            self.assertEqual({group["repeat"] for group in report["repetitions"]}, {1, 2, 3})
            for group in report["repetitions"]:
                self.assertEqual(group["scheduled_runs"], 80)
                for metric in ("first_equivalence", "first_simplification", "final_simplification"):
                    self.assert_metric(group[metric], 0, 80)
                self.assertEqual(group["resources"]["calls"], 0)


if __name__ == "__main__":
    unittest.main()

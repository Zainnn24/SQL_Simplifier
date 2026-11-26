"""Exercise generic benchmark gates with temporary author and review fixtures.

Temporary approvals and injected APIs below are validation controls, not actual
peer decisions, production-verifier evidence, or additions to the 32 real tasks.
"""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from evaluation.benchmark_check import validate_benchmark


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_REVIEWER = {"llm": "verifier", "verifier": "evaluation", "evaluation": "llm"}


def write_jsonl(path, records):
    path.write_text("".join(json.dumps(record, ensure_ascii=True) + "\n" for record in records), encoding="utf-8")


def seed_tasks_from_three_owners():
    """Return local test copies of existing A1, B3 and D3 seed records."""
    return [
        {
            "task_id": "A1-01", "family": "A1", "split": "test",
            "schema_sql": "CREATE TABLE tickets (row_id BIGINT NOT NULL, priority BIGINT NOT NULL)",
            "original_sql": "SELECT row_id FROM tickets WHERE priority >= 4 AND priority >= 4",
            "reference_sql": "SELECT row_id FROM tickets WHERE priority >= 4",
            "sample_rows": [{"row_id": 1, "priority": 3}, {"row_id": 2, "priority": 4}, {"row_id": 3, "priority": 5}],
            "expected_rows": [[2], [3]],
            "derivation": "Repeating the same Boolean condition in a conjunction does not change any legal row's outcome.",
            "source": "Temporary unit fixture copied from the A1 SQL seed; not a new author contribution.",
            "error_exposed": "Returning TRUE would incorrectly include row 1 with priority 3.",
        },
        json.loads((ROOT / "evaluation/B3-01.json").read_text(encoding="utf-8")),
        {
            "task_id": "D3-01", "family": "D3", "split": "test",
            "schema_sql": "CREATE TABLE batches (row_id BIGINT NOT NULL, quantity BIGINT NOT NULL)",
            "original_sql": "SELECT row_id FROM batches WHERE quantity > 12 AND quantity < 13",
            "reference_sql": "SELECT row_id FROM batches WHERE FALSE",
            "sample_rows": [{"row_id": 1, "quantity": 11}, {"row_id": 2, "quantity": 12}, {"row_id": 3, "quantity": 13}, {"row_id": 4, "quantity": 14}],
            "expected_rows": [],
            "derivation": "No integer lies strictly between consecutive integers 12 and 13, so the predicate is false on every legal row.",
            "source": "Temporary unit fixture copied from the D3 SQL seed; not a new author contribution.",
            "error_exposed": "Using quantity >= 12 AND quantity <= 13 would incorrectly include rows 2 and 3.",
        },
    ]


def pending_review(task_id, author_role, *, check_removal_test=None):
    return {
        "task_id": task_id, "author_role": author_role,
        "reviewer_role": EXPECTED_REVIEWER[author_role], "review_status": "PENDING",
        "review_notes": None, "check_removal_test": check_removal_test,
    }


def e2_check_removal_fixture():
    """Use an isolated E2 variation with a second CHECK and ordered projection."""
    task = {
        "task_id": "E2-02", "family": "E2", "split": "dev",
        "schema_sql": (
            "CREATE TABLE jobs (row_id BIGINT NOT NULL, start_day BIGINT NOT NULL, "
            "end_day BIGINT NOT NULL, CHECK (start_day <= end_day), CHECK (row_id >= 0))"
        ),
        "original_sql": "SELECT row_id, start_day FROM jobs WHERE start_day >= 10 AND end_day >= 10",
        "reference_sql": "SELECT row_id, start_day FROM jobs WHERE start_day >= 10",
        "sample_rows": [
            {"row_id": 1, "start_day": 9, "end_day": 9},
            {"row_id": 2, "start_day": 9, "end_day": 10},
            {"row_id": 3, "start_day": 10, "end_day": 10},
            {"row_id": 4, "start_day": 10, "end_day": 12},
            {"row_id": 5, "start_day": 11, "end_day": 12},
        ],
        "expected_rows": [[3, 10], [4, 10], [5, 11]],
        "derivation": "The ordering CHECK implies end_day >= 10 whenever start_day >= 10. Removing the implied conjunct preserves every legal row and the fixed two-column projection.",
        "source": "Temporary E2 check-removal unit fixture derived from the SQL seed; never an additional real benchmark contribution.",
        "error_exposed": "Keeping only end_day >= 10 would include sample row 2, which fails the original start bound.",
    }
    removal = {
        "removed_check_sql": "CHECK (start_day <= end_day)",
        "schema_without_check_sql": (
            "CREATE TABLE jobs (row_id BIGINT NOT NULL, start_day BIGINT NOT NULL, "
            "end_day BIGINT NOT NULL, CHECK (row_id >= 0))"
        ),
        "row": {"row_id": 1, "start_day": 10, "end_day": 9},
        "original_truth": False, "reference_truth": True,
        "original_rows": [], "reference_rows": [[1, 10]],
    }
    return task, pending_review("E2-02", "verifier", check_removal_test=removal)


class BenchmarkCheckTests(unittest.TestCase):
    def packet(self, tasks=None, reviews=None):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        tasks = seed_tasks_from_three_owners() if tasks is None else deepcopy(tasks)
        if reviews is None:
            roles = {"A1-01": "llm", "B3-01": "evaluation", "D3-01": "verifier"}
            reviews = [pending_review(task["task_id"], roles[task["task_id"]]) for task in tasks]
        packet = SimpleNamespace(
            directory=directory, manifest=directory / "benchmark.jsonl",
            review_path=directory / "benchmark_reviews.jsonl", tasks=tasks, reviews=deepcopy(reviews),
        )
        self.save(packet)
        return packet

    def save(self, packet):
        write_jsonl(packet.manifest, packet.tasks)
        write_jsonl(packet.review_path, packet.reviews)

    def validate(self, packet, **kwargs):
        return validate_benchmark(packet.manifest, reviews=packet.review_path, **kwargs)

    def assert_invalid(self, report):
        self.assertEqual(report["status"], "INVALID", report)
        self.assertEqual(report["exit_code"], 1)
        self.assertTrue(report["issues"])

    def assert_blocked(self, report):
        self.assertEqual(report["status"], "FORMAL_BLOCKED", report)
        self.assertEqual(report["exit_code"], 2)
        self.assertTrue(report["issues"])

    def assert_removal_error(self, report):
        self.assert_invalid(report)
        self.assertTrue(any(
            issue["code"] == "CHECK_REMOVAL_ERROR" and issue.get("task_id") == "E2-02"
            for issue in report["issues"]
        ), report["issues"])

    def test_draft_accepts_seed_records_from_all_three_owners_without_claiming_100(self):
        report = self.validate(self.packet())
        self.assertEqual(report["status"], "DRAFT", report)
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["coverage"]["received"], 3)
        self.assertEqual(report["coverage"]["expected"], 100)
        self.assertEqual(len(report["coverage"]["missing_ids"]), 97)
        self.assertEqual(report["coverage"]["by_owner"], {"llm": 1, "evaluation": 1, "verifier": 1})
        self.assertEqual(report["coverage"]["split_counts"], {"dev": 1, "test": 2})

    def test_partial_collection_is_blocked_in_formal_mode(self):
        report = self.validate(self.packet(), mode="formal")
        self.assert_blocked(report)
        self.assertEqual(report["coverage"]["received"], 3)
        self.assertEqual(len(report["coverage"]["missing_ids"]), 97)

    def test_duplicate_task_ids_are_invalid_instead_of_counted_as_coverage(self):
        packet = self.packet()
        packet.tasks.append(deepcopy(packet.tasks[0]))
        self.save(packet)
        self.assert_invalid(self.validate(packet))

    def test_cosmetic_renaming_constant_shift_and_commutation_flag_both_ids(self):
        packet = self.packet()
        duplicate = {
            "task_id": "B3-02", "family": "B3", "split": "dev",
            "schema_sql": "CREATE TABLE renamed_pin (row_id BIGINT NOT NULL, renamed_value BIGINT NOT NULL)",
            "original_sql": "SELECT row_id FROM renamed_pin WHERE renamed_value <= -6 AND renamed_value >= -6",
            "reference_sql": "SELECT row_id FROM renamed_pin WHERE renamed_value = -6",
            "sample_rows": [
                {"row_id": 1, "renamed_value": -7}, {"row_id": 2, "renamed_value": -6},
                {"row_id": 3, "renamed_value": -5}, {"row_id": 4, "renamed_value": -6},
            ],
            "expected_rows": [[2], [4]],
            "derivation": "These inclusive bounds pin renamed_value to -6. This is deliberately a renamed and constant-shifted duplicate of B3-01 for a screening unit test.",
            "source": "Temporary cosmetic-duplicate control, not an additional benchmark task.",
            "error_exposed": "Keeping only the lower bound includes row 3; keeping only the upper bound includes row 1.",
        }
        packet.tasks.append(duplicate)
        packet.reviews.append(pending_review("B3-02", "evaluation"))
        self.save(packet)
        report = self.validate(packet)
        self.assert_invalid(report)
        expected_ids = {"B3-01", "B3-02"}
        self.assertTrue(any(expected_ids.issubset(ids) for ids in map(set, report["duplicate_signatures"])))
        matches = [issue for issue in report["issues"] if issue["code"] == "DUPLICATE_STRUCTURE"]
        self.assertTrue(any(expected_ids.issubset(set(issue["task_ids"])) for issue in matches), matches)
        self.assertTrue(all("duplicate" in issue["message"].lower() for issue in matches))

    def test_task_family_split_and_id_must_agree(self):
        for field, value in (("family", "C1"), ("split", "test"), ("task_id", "not-a-family-id")):
            with self.subTest(field=field):
                packet = self.packet()
                packet.tasks[1][field] = value
                self.save(packet)
                self.assert_invalid(self.validate(packet))

    def test_pending_feedback_is_valid_draft_metadata(self):
        packet = self.packet()
        packet.reviews[0]["review_notes"] = "Temporary changes-requested fixture: explain the endpoint."
        self.save(packet)
        report = self.validate(packet)
        self.assertEqual(report["status"], "DRAFT", report)
        self.assertEqual(report["exit_code"], 0)

    def test_approved_records_require_nonempty_notes(self):
        for notes in (None, "", "   "):
            with self.subTest(notes=notes):
                packet = self.packet()
                packet.reviews[0].update({"review_status": "APPROVED", "review_notes": notes})
                self.save(packet)
                self.assert_invalid(self.validate(packet))

    def test_temporary_approval_with_notes_still_only_produces_a_draft(self):
        packet = self.packet()
        packet.reviews[0].update({
            "review_status": "APPROVED",
            "review_notes": "Temporary approval-format control, not an actual peer decision.",
        })
        self.save(packet)
        report = self.validate(packet)
        self.assertEqual(report["status"], "DRAFT", report)
        self.assertNotEqual(report["status"], "FORMAL_PASSED")

    def test_owner_and_cross_review_assignments_are_enforced(self):
        faults = (("author_role", "evaluation"), ("reviewer_role", "llm"), ("reviewer_role", "evaluation"))
        for field, value in faults:
            with self.subTest(field=field, value=value):
                packet = self.packet()
                packet.reviews[0][field] = value
                self.save(packet)
                self.assert_invalid(self.validate(packet))

    def test_exact_check_removal_keeps_other_constraints_and_ordered_projection(self):
        task, review = e2_check_removal_fixture()
        report = self.validate(self.packet([task], [review]))
        self.assertEqual(report["status"], "DRAFT", report)
        self.assertEqual(report["exit_code"], 0)
        self.assertFalse(any(issue["code"] == "CHECK_REMOVAL_ERROR" for issue in report["issues"]))

    def test_exact_e2_seed_can_be_checked_in_a_temporary_offline_manifest(self):
        path = ROOT / "evaluation/scoring_examples/README_E2/manifest.jsonl"
        before = path.read_bytes()
        tasks = [json.loads(line) for line in before.decode("utf-8").splitlines() if line.strip()]
        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertEqual(task["task_id"], "E2-01")
        removal = {
            "removed_check_sql": "CHECK (start_day <= end_day)",
            "schema_without_check_sql": (
                "CREATE TABLE jobs (row_id BIGINT NOT NULL, start_day BIGINT NOT NULL, end_day BIGINT NOT NULL)"
            ),
            "row": {"row_id": 1, "start_day": 10, "end_day": 9},
            "original_truth": False, "reference_truth": True,
            "original_rows": [], "reference_rows": [[1]],
        }
        review = pending_review("E2-01", "verifier", check_removal_test=removal)
        report = self.validate(self.packet([task], [review]))
        self.assertEqual(report["status"], "DRAFT", report)
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["seed_checks"], [{"task_id": "E2-01", "passed": True}])
        self.assertEqual(len(report["check_removal_checks"]), 1)
        check = report["check_removal_checks"][0]
        self.assertTrue(check["passed"])
        self.assertEqual(check["original_schema"]["status"], "REJECTED_CHECK")
        self.assertEqual(check["original"]["rows"], [])
        self.assertEqual(check["reference"]["rows"], [[1]])
        self.assertEqual(path.read_bytes(), before)

    def test_check_removal_rejects_a_non_distinguishing_or_illegal_witness(self):
        for row, projected in (
            ({"row_id": 1, "start_day": 9, "end_day": 10}, [[1, 9]]),
            ({"row_id": -1, "start_day": 10, "end_day": 9}, [[-1, 10]]),
        ):
            with self.subTest(row=row):
                task, review = e2_check_removal_fixture()
                review["check_removal_test"]["row"] = row
                review["check_removal_test"]["reference_rows"] = projected
                self.assert_removal_error(self.validate(self.packet([task], [review])))

    def test_check_removal_rejects_wrong_projection_column_order(self):
        task, review = e2_check_removal_fixture()
        review["check_removal_test"]["reference_rows"] = [[10, 1]]
        self.assert_removal_error(self.validate(self.packet([task], [review])))

    def test_removed_check_must_name_an_existing_exact_occurrence(self):
        task, review = e2_check_removal_fixture()
        review["check_removal_test"]["removed_check_sql"] = "CHECK (start_day < end_day)"
        self.assert_removal_error(self.validate(self.packet([task], [review])))

    def test_removed_schema_cannot_silently_drop_an_additional_check(self):
        task, review = e2_check_removal_fixture()
        review["check_removal_test"]["schema_without_check_sql"] = (
            "CREATE TABLE jobs (row_id BIGINT NOT NULL, start_day BIGINT NOT NULL, end_day BIGINT NOT NULL)"
        )
        self.assert_removal_error(self.validate(self.packet([task], [review])))

    def test_e_family_cannot_be_approved_without_check_removal_evidence(self):
        task, review = e2_check_removal_fixture()
        review.update({
            "review_status": "APPROVED",
            "review_notes": "Temporary approval-format control only, not a peer decision.",
            "check_removal_test": None,
        })
        self.assert_invalid(self.validate(self.packet([task], [review])))

    def test_missing_or_invented_experiment_a_evidence_cannot_enable_formal_acceptance(self):
        examples = (None, {"passed": True}, {"production": {"run_status": "NOT_RUN"}})
        for evidence in examples:
            with self.subTest(evidence=evidence):
                packet = self.packet()
                path = None
                if evidence is not None:
                    path = packet.directory / "synthetic-not-valid-experiment-a.json"
                    path.write_text(json.dumps(evidence), encoding="utf-8")
                report = self.validate(packet, mode="formal", experiment_a_report=path)
                self.assert_blocked(report)
                self.assertFalse(report["production"]["test_standin"])
                self.assertFalse(report["experiment_a_evidence"]["accepted"])
                self.assertEqual(report["production"]["run_status"], "NOT_RUN")

    def test_explicit_test_standin_exercises_no_shrink_rejection_without_formal_pass(self):
        packet = self.packet()
        api = SimpleNamespace(
            __benchmark_test_standin__=True,
            validate_input=Mock(return_value={"status": "READY", "original_nodes": 7, "diagnostic": None}),
            verify=Mock(return_value={
                "status": "EQUIVALENT", "original_nodes": 7, "candidate_nodes": 7,
                "diagnostic": None, "witness": None,
            }),
        )
        report = self.validate(packet, mode="formal", api=api)
        self.assert_blocked(report)
        self.assertTrue(api.verify.called)
        self.assertTrue(report["production"]["test_standin"])
        self.assertTrue(any(issue["code"] == "TEST_STANDIN" for issue in report["issues"]))
        self.assertTrue(any(issue["code"] == "REFERENCE_NOT_SMALLER" for issue in report["issues"]), report["issues"])

    def test_correct_smaller_test_standin_still_cannot_establish_formal_acceptance(self):
        task = json.loads((ROOT / "evaluation/B3-01.json").read_text(encoding="utf-8"))
        packet = self.packet([task], [pending_review("B3-01", "evaluation")])
        api = SimpleNamespace(
            __benchmark_test_standin__=True,
            validate_input=Mock(return_value={"status": "READY", "original_nodes": 7, "diagnostic": None}),
            verify=Mock(return_value={
                "status": "EQUIVALENT", "original_nodes": 7, "candidate_nodes": 3,
                "diagnostic": None, "witness": None,
            }),
        )
        report = self.validate(packet, mode="formal", api=api)
        self.assert_blocked(report)
        api.validate_input.assert_called_once()
        api.verify.assert_called_once()
        self.assertTrue(report["production"]["test_standin"])
        self.assertEqual(report["production"]["run_status"], "TEST_ONLY")
        self.assertEqual(len(report["production"]["checks"]), 1)
        result = report["production"]["checks"][0]["result"]
        self.assertEqual((result["original_nodes"], result["candidate_nodes"]), (7, 3))
        self.assertTrue(any(issue["code"] == "TEST_STANDIN" for issue in report["issues"]))
        self.assertFalse(any(issue["code"] in {"REFERENCE_NOT_SMALLER", "REFERENCE_VERIFICATION_ERROR"}
                             for issue in report["issues"]), report["issues"])

    def test_unmarked_injected_api_is_rejected_without_being_called(self):
        packet = self.packet()
        api = SimpleNamespace(validate_input=Mock(), verify=Mock())
        try:
            report = self.validate(packet, mode="formal", api=api)
        except ValueError:
            pass
        else:
            self.assertNotEqual(report["status"], "FORMAL_PASSED")
            self.assertNotEqual(report["exit_code"], 0)
        api.validate_input.assert_not_called()
        api.verify.assert_not_called()

    def test_validation_preserves_input_files_real_tasks_reviews_and_seed(self):
        packet = self.packet()
        actual = [
            ROOT / "evaluation" / f"{family}-{variant:02d}.json"
            for family in ("B3", "B4", "B5", "C1", "C2", "C3", "C4", "C5")
            for variant in range(1, 5)
        ]
        protected = [packet.manifest, packet.review_path, *actual,
                     ROOT / "evaluation/benchmark_reviews.jsonl",
                     ROOT / "data/SQL_Predicate_Family_Examples.sql"]
        before = {path: path.read_bytes() for path in protected}
        draft = self.validate(packet)
        formal = self.validate(packet, mode="formal")
        self.assertEqual(draft["status"], "DRAFT", draft)
        self.assert_blocked(formal)
        self.assertEqual(before, {path: path.read_bytes() for path in protected})


if __name__ == "__main__":
    unittest.main()

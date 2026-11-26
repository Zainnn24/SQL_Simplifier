"""Exercise the B3/C1 development packet before independent review."""

from collections import Counter
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from evaluation.sample_check import check_task, format_check, load_task


ROOT = Path(__file__).resolve().parents[2]
TASK_IDS = tuple(f"{family}-{variant:02d}" for family in ("B3", "C1") for variant in range(1, 5))
SAMPLE_COUNTS = {
    "B3-01": 4, "B3-02": 3, "B3-03": 8, "B3-04": 6,
    "C1-01": 10, "C1-02": 10, "C1-03": 10, "C1-04": 11,
}
# Author-derived expected IDs are independent of DuckDB execution.
EXPECTED_IDS = {
    "B3-01": (2, 4),
    "B3-02": (1, 3),
    "B3-03": (2, 5, 6, 7),
    "B3-04": (3, 4),
    "C1-01": (2, 3, 4, 5, 6, 7, 10),
    "C1-02": (2, 3, 4, 5, 6, 7, 8, 9),
    "C1-03": (2, 3, 4, 5, 7, 8, 9),
    "C1-04": (2, 3, 5, 6, 9, 10),
}
# Each tuple is (wrong reference SQL, missing projected IDs, unexpected IDs).
# Wrong candidates exist only in temporary test copies, not the task records.
NEGATIVE_REWRITES = {
    "B3-02": (
        ("SELECT row_id FROM readings WHERE TRUE", (), (2,)),
    ),
    "B3-03": (
        ("SELECT row_id FROM pinned_ranges WHERE x >= 2 AND x <= 5", (2,), ()),
        ("SELECT row_id FROM pinned_ranges WHERE x = -4", (5, 6, 7), ()),
    ),
    "B3-04": (
        ("SELECT row_id FROM pinned_controls WHERE x = -3", (), (2, 6)),
    ),
    "C1-02": (
        ("SELECT row_id FROM chained_ranges WHERE x >= 0 AND x <= 7", (8, 9), ()),
    ),
    "C1-03": (
        ("SELECT row_id FROM separated_ranges WHERE x >= -4 AND x <= 7", (), (6,)),
        ("SELECT row_id FROM separated_ranges WHERE x >= -4 AND x <= 3", (7, 8, 9), ()),
    ),
    "C1-04": (
        (
            "SELECT row_id FROM guarded_ranges "
            "WHERE (reading >= 10 AND reading <= 20) OR reading >= 40",
            (), (4, 11),
        ),
    ),
}


class DevelopmentFamilyTests(unittest.TestCase):
    def test_eight_records_have_complete_fields_and_unique_dev_ids(self):
        required = {
            "task_id", "family", "split", "schema_sql", "original_sql", "reference_sql",
            "sample_rows", "expected_rows", "derivation", "source", "error_exposed",
        }
        seen = set()
        for task_id in TASK_IDS:
            with self.subTest(task_id=task_id):
                path = ROOT / "evaluation" / f"{task_id}.json"
                task = load_task(path)
                self.assertTrue(required.issubset(task))
                self.assertTrue(all(task[field] is not None for field in required))
                self.assertEqual(task["task_id"], task_id)
                self.assertEqual(task["family"], task_id.split("-")[0])
                self.assertEqual(task["split"], "dev")
                self.assertNotIn(task_id, seen)
                seen.add(task_id)
                self.assertTrue(path.read_text(encoding="utf-8").isascii())
                for field in ("derivation", "source", "error_exposed"):
                    self.assertIsInstance(task[field], str)
                    self.assertTrue(task[field].strip())
        self.assertEqual(len(seen), 8)

    def test_b3_02_matches_the_complete_readme_draft(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        section = readme.split("#### B3-02 authoring example", 1)[1]
        draft = json.loads(section.split("```json\n", 1)[1].split("```", 1)[0])
        self.assertEqual(load_task(ROOT / "evaluation/B3-02.json"), draft)

    def test_eight_sample_results_match_independently_derived_ids(self):
        for task_id in TASK_IDS:
            with self.subTest(task_id=task_id):
                task = load_task(ROOT / "evaluation" / f"{task_id}.json")
                expected = Counter((value,) for value in EXPECTED_IDS[task_id])
                self.assertEqual(len(task["sample_rows"]), SAMPLE_COUNTS[task_id])
                self.assertEqual(Counter(tuple(row) for row in task["expected_rows"]), expected)
                checks = check_task(task)
                self.assertEqual([result.source for result in checks], ["original_sql", "reference_sql"])
                for result in checks:
                    self.assertTrue(result.passed, format_check(result))
                    self.assertEqual(Counter(result.actual_rows), expected)

    def assert_development_review_assignments(self, path):
        reviews = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        required = {
            "task_id", "author_role", "reviewer_role", "review_status",
            "review_notes", "check_removal_test",
        }
        for task_id in TASK_IDS:
            matches = [record for record in reviews if record["task_id"] == task_id]
            self.assertEqual(len(matches), 1, task_id)
            review = matches[0]
            self.assertTrue(required.issubset(review), task_id)
            self.assertEqual(review["author_role"], "evaluation")
            self.assertEqual(review["reviewer_role"], "llm")
            self.assertIn(review["review_status"], ("PENDING", "APPROVED"))
            notes = review["review_notes"]
            self.assertTrue(notes is None or isinstance(notes, str))
            if review["review_status"] == "APPROVED":
                self.assertIsInstance(notes, str)
                self.assertTrue(notes.strip())
            self.assertIsNone(review["check_removal_test"])

    def test_review_assignments_cover_all_eight_development_tasks(self):
        self.assert_development_review_assignments(ROOT / "evaluation/benchmark_reviews.jsonl")

    def test_pending_feedback_is_allowed_in_temporary_review_records(self):
        source = ROOT / "evaluation/benchmark_reviews.jsonl"
        before = source.read_bytes()
        reviews = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
        selected = next(record for record in reviews if record["task_id"] == "B3-01")
        selected["review_status"] = "PENDING"
        selected["review_notes"] = "Changes requested: explain the endpoint and retain the boundary sample."
        with TemporaryDirectory() as directory:
            path = Path(directory) / "benchmark_reviews.jsonl"
            path.write_text("".join(json.dumps(record) + "\n" for record in reviews), encoding="utf-8")
            self.assert_development_review_assignments(path)
        self.assertEqual(source.read_bytes(), before)

    def test_approved_reviews_require_nonempty_notes_in_temporary_records(self):
        source = ROOT / "evaluation/benchmark_reviews.jsonl"
        before = source.read_bytes()
        reviews = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
        selected = next(record for record in reviews if record["task_id"] == "B3-01")
        selected["review_status"] = "APPROVED"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "benchmark_reviews.jsonl"
            for notes in (None, "", "   "):
                with self.subTest(notes=notes):
                    selected["review_notes"] = notes
                    path.write_text("".join(json.dumps(record) + "\n" for record in reviews), encoding="utf-8")
                    with self.assertRaises(AssertionError):
                        self.assert_development_review_assignments(path)
            selected["review_notes"] = "Independently reviewed: both inclusive bounds identify the same point."
            path.write_text("".join(json.dumps(record) + "\n" for record in reviews), encoding="utf-8")
            self.assert_development_review_assignments(path)
        self.assertEqual(source.read_bytes(), before)

    def assert_wrong_references_detected(self, task_id):
        source_path = ROOT / "evaluation" / f"{task_id}.json"
        source_bytes = source_path.read_bytes()
        for wrong_sql, missing_ids, unexpected_ids in NEGATIVE_REWRITES[task_id]:
            with self.subTest(task_id=task_id, wrong_sql=wrong_sql):
                task = load_task(source_path)
                task["reference_sql"] = wrong_sql
                with TemporaryDirectory() as directory:
                    temporary_path = Path(directory) / f"{task_id}.json"
                    temporary_path.write_text(json.dumps(task), encoding="utf-8")
                    checks = check_task(load_task(temporary_path))
                self.assertEqual([result.source for result in checks], ["original_sql", "reference_sql"])
                original, reference = checks
                self.assertTrue(original.passed, format_check(original))
                self.assertFalse(reference.passed)
                self.assertIsNone(reference.error, format_check(reference))
                self.assertEqual(reference.missing, Counter((value,) for value in missing_ids))
                self.assertEqual(reference.unexpected, Counter((value,) for value in unexpected_ids))
                self.assertTrue(reference.missing or reference.unexpected)
        self.assertEqual(source_path.read_bytes(), source_bytes)

    def test_b3_02_wrong_true_keeps_the_excluded_endpoint(self):
        self.assert_wrong_references_detected("B3-02")

    def test_b3_03_dropping_either_live_branch_is_detected(self):
        self.assert_wrong_references_detected("B3-03")

    def test_b3_04_dropping_the_second_column_condition_is_detected(self):
        self.assert_wrong_references_detected("B3-04")

    def test_c1_02_merging_only_the_first_two_intervals_is_detected(self):
        self.assert_wrong_references_detected("C1-02")

    def test_c1_03_filling_the_gap_or_dropping_the_tail_is_detected(self):
        self.assert_wrong_references_detected("C1-03")

    def test_c1_04_dropping_the_second_column_guard_is_detected(self):
        self.assert_wrong_references_detected("C1-04")


if __name__ == "__main__":
    unittest.main()

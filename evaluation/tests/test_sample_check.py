"""Exercise real DuckDB observations and preserve multiset semantics."""

from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from evaluation.sample_check import check_task, format_check, load_task, main


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EVALUATION_DIR = REPOSITORY_ROOT / "evaluation"
SEED_FILE = REPOSITORY_ROOT / "data" / "SQL_Predicate_Family_Examples.sql"


def duplicate_projection_task():
    """Return distinct input rows that project to the same output row."""
    return {
        "task_id": "duplicate-projection",
        "family": "B3",
        "split": "dev",
        "schema_sql": (
            "CREATE TABLE t (row_id BIGINT NOT NULL, x BIGINT NOT NULL)"
        ),
        "original_sql": "SELECT x FROM t WHERE TRUE",
        "reference_sql": "SELECT x FROM t WHERE row_id = 1",
        "sample_rows": [{"row_id": 1, "x": 7}, {"row_id": 2, "x": 7}],
        "expected_rows": [[7], [7]],
        "derivation": "This deliberately wrong reference drops one occurrence.",
        "source": "Independent multiset regression fixture.",
        "error_exposed": "Set comparison would conceal a missing duplicate.",
    }


class SampleCheckTests(unittest.TestCase):
    def find_check(self, checks, source):
        matches = [result for result in checks if result.source == source]
        self.assertEqual(len(matches), 1, f"Expected one {source} result")
        return matches[0]

    def assert_all_pass(self, checks):
        self.assertTrue(checks, "The checker must produce observations")
        for result in checks:
            self.assertTrue(result.passed, format_check(result))
            self.assertIsNone(result.error)

    def test_canonical_tasks_match_seed_and_both_queries(self):
        expected = {
            "B3-01": Counter({(2,): 1, (4,): 1}),
            "C1-01": Counter(
                {(2,): 1, (3,): 1, (4,): 1, (5,): 1,
                 (6,): 1, (7,): 1, (10,): 1}
            ),
        }
        for task_id, expected_rows in expected.items():
            with self.subTest(task_id=task_id):
                task = load_task(EVALUATION_DIR / f"{task_id}.json")
                self.assertIsInstance(task, dict)
                checks = check_task(task, seed_file=SEED_FILE)
                self.assert_all_pass(checks)
                self.assertTrue(self.find_check(checks, "seed_rows").passed)
                for source in ("original_sql", "reference_sql"):
                    result = self.find_check(checks, source)
                    self.assertEqual(result.task_id, task_id)
                    self.assertEqual(Counter(result.actual_rows), expected_rows)

    def test_temporary_c1_copy_exposes_missing_independent_branch(self):
        original_path = EVALUATION_DIR / "C1-01.json"
        original_bytes = original_path.read_bytes()
        task = load_task(original_path)
        task["reference_sql"] = (
            "SELECT row_id FROM samples WHERE reading >= 10 AND reading <= 25"
        )
        with TemporaryDirectory() as directory:
            copy_path = Path(directory) / "C1-01.json"
            copy_path.write_text(json.dumps(task), encoding="utf-8")
            checks = check_task(load_task(copy_path))
        self.assertTrue(self.find_check(checks, "original_sql").passed)
        reference = self.find_check(checks, "reference_sql")
        self.assertFalse(reference.passed)
        self.assertIsNone(reference.error)
        self.assertEqual(reference.missing, Counter({(10,): 1}))
        self.assertEqual(reference.unexpected, Counter())
        self.assertEqual(original_path.read_bytes(), original_bytes)

    def test_missing_projected_duplicate_is_counted(self):
        checks = check_task(duplicate_projection_task())
        self.assertTrue(self.find_check(checks, "original_sql").passed)
        reference = self.find_check(checks, "reference_sql")
        self.assertFalse(reference.passed)
        self.assertEqual(reference.actual_rows, ((7,),))
        self.assertEqual(reference.missing, Counter({(7,): 1}))
        self.assertEqual(reference.unexpected, Counter())

    def test_matching_wrong_queries_still_fail_author_expectations(self):
        task = load_task(EVALUATION_DIR / "B3-01.json")
        wrong_sql = "SELECT row_id FROM parcels WHERE weight_kg > 15"
        task["original_sql"] = wrong_sql
        task["reference_sql"] = wrong_sql
        checks = check_task(task)
        for source in ("original_sql", "reference_sql"):
            with self.subTest(source=source):
                result = self.find_check(checks, source)
                self.assertFalse(result.passed)
                self.assertEqual(result.actual_rows, ((3,),))
                self.assertEqual(result.missing, Counter({(2,): 1, (4,): 1}))
                self.assertEqual(result.unexpected, Counter({(3,): 1}))

    def test_input_and_result_row_order_do_not_affect_comparison(self):
        task = load_task(EVALUATION_DIR / "B3-01.json")
        task["sample_rows"].reverse()
        task["expected_rows"].reverse()
        self.assert_all_pass(check_task(task, seed_file=SEED_FILE))

    def test_input_object_key_order_does_not_reorder_columns(self):
        task = load_task(EVALUATION_DIR / "B3-01.json")
        task["sample_rows"] = [
            {"weight_kg": row["weight_kg"], "row_id": row["row_id"]}
            for row in task["sample_rows"]
        ]
        self.assert_all_pass(check_task(task, seed_file=SEED_FILE))

    def test_column_order_within_projected_rows_is_preserved(self):
        task = duplicate_projection_task()
        task["original_sql"] = "SELECT row_id, x FROM t WHERE TRUE"
        task["reference_sql"] = "SELECT x, row_id FROM t WHERE TRUE"
        task["expected_rows"] = [[1, 7], [2, 7]]
        checks = check_task(task)
        self.assertTrue(self.find_check(checks, "original_sql").passed)
        reference = self.find_check(checks, "reference_sql")
        self.assertFalse(reference.passed)
        self.assertEqual(Counter(reference.actual_rows), Counter({(7, 1): 1, (7, 2): 1}))
        self.assertEqual(reference.missing, Counter({(1, 7): 1, (2, 7): 1}))
        self.assertEqual(reference.unexpected, Counter({(7, 1): 1, (7, 2): 1}))

    def test_unexpected_projected_duplicate_is_counted(self):
        task = duplicate_projection_task()
        task["expected_rows"] = [[7]]
        checks = check_task(task)
        original = self.find_check(checks, "original_sql")
        self.assertFalse(original.passed)
        self.assertEqual(original.missing, Counter())
        self.assertEqual(original.unexpected, Counter({(7,): 1}))
        self.assertTrue(self.find_check(checks, "reference_sql").passed)

    def test_seed_detects_column_drift_in_an_excluded_row(self):
        task = load_task(EVALUATION_DIR / "B3-01.json")
        task["sample_rows"][0]["weight_kg"] = 13
        checks = check_task(task, seed_file=SEED_FILE)
        seed = self.find_check(checks, "seed_rows")
        self.assertFalse(seed.passed)
        self.assertEqual(seed.missing, Counter({(1, 14): 1}))
        self.assertEqual(seed.unexpected, Counter({(1, 13): 1}))
        self.assertTrue(self.find_check(checks, "original_sql").passed)
        self.assertTrue(self.find_check(checks, "reference_sql").passed)

    def test_seed_compares_whole_rows_not_independent_column_multisets(self):
        task = load_task(EVALUATION_DIR / "B3-01.json")
        task["sample_rows"][0]["weight_kg"] = 16
        task["sample_rows"][2]["weight_kg"] = 14
        checks = check_task(task, seed_file=SEED_FILE)
        seed = self.find_check(checks, "seed_rows")
        self.assertFalse(seed.passed)
        self.assertEqual(seed.missing, Counter({(1, 14): 1, (3, 16): 1}))
        self.assertEqual(seed.unexpected, Counter({(1, 16): 1, (3, 14): 1}))
        self.assertTrue(self.find_check(checks, "original_sql").passed)
        self.assertTrue(self.find_check(checks, "reference_sql").passed)

    def test_seed_detects_lost_duplicate_input_even_when_outputs_match(self):
        seed_text = SEED_FILE.read_text(encoding="utf-8")
        start = seed_text.index("-- B3.")
        end = seed_text.index("-- B4.", start)
        block = seed_text[start:end]
        self.assertIn("    (1, 14),", block)
        duplicated = block.replace("    (1, 14),", "    (1, 14),\n    (1, 14),", 1)
        modified_seed = seed_text[:start] + duplicated + seed_text[end:]
        with TemporaryDirectory() as directory:
            seed_path = Path(directory) / "seeds.sql"
            seed_path.write_text(modified_seed, encoding="utf-8")
            task = load_task(EVALUATION_DIR / "B3-01.json")
            checks = check_task(task, seed_file=seed_path)
        seed = self.find_check(checks, "seed_rows")
        self.assertFalse(seed.passed)
        self.assertEqual(seed.missing, Counter({(1, 14): 1}))
        self.assertEqual(seed.unexpected, Counter())
        self.assertTrue(self.find_check(checks, "original_sql").passed)
        self.assertTrue(self.find_check(checks, "reference_sql").passed)

    def test_formatted_failure_names_source_and_multiplicity(self):
        reference = self.find_check(
            check_task(duplicate_projection_task()), "reference_sql"
        )
        formatted = format_check(reference)
        self.assertIn("duplicate-projection", formatted)
        self.assertIn("reference_sql", formatted)
        self.assertIn("missing", formatted)
        self.assertIn('"row": [7]', formatted)
        self.assertIn('"count": 1', formatted)

    def test_default_cli_checks_both_canonical_tasks_successfully(self):
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            status = main(["check-samples"])
        self.assertEqual(status, 0, output.getvalue())
        self.assertIn("B3-01", output.getvalue())
        self.assertIn("C1-01", output.getvalue())
        self.assertIn("seed_rows", output.getvalue())

    def test_custom_cli_reports_failed_query_without_default_seed_check(self):
        with TemporaryDirectory() as directory:
            task_path = Path(directory) / "duplicate-projection.json"
            task_path.write_text(json.dumps(duplicate_projection_task()), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output), redirect_stderr(output):
                status = main(["check-samples", "--tasks", str(task_path)])
        self.assertEqual(status, 1, output.getvalue())
        self.assertIn("duplicate-projection", output.getvalue())
        self.assertIn("reference_sql", output.getvalue())
        self.assertIn("missing", output.getvalue())
        self.assertNotIn("seed_rows", output.getvalue())

    def test_custom_cli_accepts_multiple_files_and_explicit_seed(self):
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            status = main([
                "check-samples", "--tasks",
                str(EVALUATION_DIR / "B3-01.json"),
                str(EVALUATION_DIR / "C1-01.json"),
                "--seed-file", str(SEED_FILE),
            ])
        self.assertEqual(status, 0, output.getvalue())
        self.assertIn("B3-01", output.getvalue())
        self.assertIn("C1-01", output.getvalue())
        self.assertIn("seed_rows", output.getvalue())

    def test_invalid_original_query_does_not_prevent_reference_observation(self):
        for invalid_sql in (
            "SELECT row_id FROM parcels WHERE (",
            "DELETE FROM parcels",
        ):
            with self.subTest(original_sql=invalid_sql):
                task = load_task(EVALUATION_DIR / "B3-01.json")
                task["original_sql"] = invalid_sql
                checks = check_task(task)
                original = self.find_check(checks, "original_sql")
                self.assertFalse(original.passed)
                self.assertIsNotNone(original.error)
                reference = self.find_check(checks, "reference_sql")
                self.assertTrue(reference.passed, format_check(reference))
                self.assertEqual(Counter(reference.actual_rows), Counter({(2,): 1, (4,): 1}))

    def test_cli_continues_after_null_sample_rows_in_first_task(self):
        invalid_task = load_task(EVALUATION_DIR / "B3-01.json")
        invalid_task["task_id"] = "invalid-samples"
        invalid_task["sample_rows"] = None
        with TemporaryDirectory() as directory:
            invalid_path = Path(directory) / "invalid-samples.json"
            invalid_path.write_text(json.dumps(invalid_task), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output), redirect_stderr(output):
                status = main([
                    "check-samples", "--tasks", str(invalid_path),
                    str(EVALUATION_DIR / "B3-01.json"),
                ])
        self.assertEqual(status, 1, output.getvalue())
        self.assertIn("FAIL invalid-samples input:", output.getvalue())
        self.assertIn("PASS B3-01 original_sql:", output.getvalue())
        self.assertIn("PASS B3-01 reference_sql:", output.getvalue())


if __name__ == "__main__":
    unittest.main()

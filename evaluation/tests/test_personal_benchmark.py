"""Data-driven author regressions for Martin's 32-task offline collection."""

from collections import Counter
import unittest

from evaluation.author_checks import (
    FAMILIES, NEW_FAMILIES, TASK_IDS, audit_structure, author_node_count,
    check_seed_sql, check_wrong_reference, duplicate_groups, load_evidence,
    load_tasks, sql_tokens, structural_signature,
)
from evaluation.sample_check import DEFAULT_SEEDS, check_task, format_check


class PersonalBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tasks = load_tasks()
        cls.evidence = load_evidence()

    def test_unique_ids_splits_and_review_assignments(self):
        statuses = audit_structure(self.tasks, self.evidence)
        self.assertEqual(sum(statuses.values()), 32)
        self.assertEqual(Counter(task["split"] for task in self.tasks.values()), {"dev": 8, "test": 24})
        self.assertEqual(Counter(task["family"] for task in self.tasks.values()), {family: 4 for family in FAMILIES})

    def test_seed_ddl_and_queries_match_all_eight_sql_blocks(self):
        self.assertEqual(check_seed_sql(self.tasks), 8)

    def test_all_eight_seed_inputs_and_expected_results_match(self):
        for family in FAMILIES:
            task_id = f"{family}-01"
            with self.subTest(task_id=task_id):
                checks = check_task(self.tasks[task_id], seed_file=DEFAULT_SEEDS)
                self.assertEqual(len(checks), 3)
                self.assertTrue(all(result.passed for result in checks), "\n".join(map(format_check, checks)))

    def test_all_32_query_pairs_match_author_expected_multisets(self):
        for task_id in TASK_IDS:
            with self.subTest(task_id=task_id):
                checks = check_task(self.tasks[task_id])
                self.assertEqual(len(checks), 2)
                for result in checks:
                    self.assertTrue(result.passed, format_check(result))

    def test_all_author_node_counts_are_exact_and_strictly_smaller(self):
        for task_id in TASK_IDS:
            with self.subTest(task_id=task_id):
                task, evidence = self.tasks[task_id], self.evidence[task_id]
                original = author_node_count(task["original_sql"])
                reference = author_node_count(task["reference_sql"])
                self.assertEqual(evidence["author_nodes"], {"original": original, "reference": reference})
                self.assertLess(reference, original)
                self.assertEqual(evidence["production_verifier_status"], "PENDING_EXPERIMENT_A")
        self.assertEqual(author_node_count("SELECT x FROM t WHERE NOT (x >= -2 AND x <= -2)"), 8)

    def test_every_new_variant_has_detectable_wrong_references(self):
        covered = set()
        for task_id, evidence in self.evidence.items():
            if task_id.split("-")[0] not in NEW_FAMILIES:
                continue
            expected = Counter(map(tuple, evidence["expected_rows"]))
            self.assertEqual(expected, Counter(map(tuple, self.tasks[task_id]["expected_rows"])))
            for wrong in evidence["wrong_references"]:
                with self.subTest(task_id=task_id, wrong_sql=wrong["sql"]):
                    original, reference = check_wrong_reference(task_id, wrong)
                    self.assertTrue(original.passed)
                    self.assertFalse(reference.passed)
                    covered.add(task_id)
        expected_ids = {f"{family}-{variant:02d}" for family in NEW_FAMILIES for variant in range(2, 5)}
        self.assertEqual(covered, expected_ids)

    def test_all_literals_and_samples_are_exact_in_range_integers(self):
        minimum, maximum = -(2**63), 2**63 - 1
        for task_id, task in self.tasks.items():
            with self.subTest(task_id=task_id):
                for row in task["sample_rows"]:
                    for value in row.values():
                        self.assertIs(type(value), int)
                        self.assertLessEqual(minimum, value)
                        self.assertLessEqual(value, maximum)
                for field in ("original_sql", "reference_sql"):
                    for token in sql_tokens(task[field]):
                        if token.lstrip("-").isdigit():
                            value = int(token)
                            self.assertLessEqual(minimum, value)
                            self.assertLessEqual(value, maximum)

    def test_c3_seed_preserves_input_and_team_reference_provenance(self):
        source = self.tasks["C3-01"]["source"].lower()
        for word in ("sqlglot", "input", "team"):
            self.assertIn(word, source)
        self.assertIn("stock_delta != 0", self.tasks["C3-01"]["reference_sql"])

    def test_no_normalized_cosmetic_duplicate_originals(self):
        self.assertEqual(duplicate_groups(self.tasks), [])

    def test_duplicate_screen_detects_renaming_commutation_and_replaced_constants(self):
        first = "SELECT row_id FROM t WHERE (x >= 1 AND x <= 5) OR x = 9"
        renamed = "SELECT id FROM u WHERE 90 = b OR (50 >= b AND 10 <= b)"
        self.assertEqual(structural_signature(first), structural_signature(renamed))

    def test_duplicate_screen_preserves_adjacency_and_bigint_endpoint_roles(self):
        adjacent = "SELECT x FROM t WHERE x = 1 OR x = 2"
        separated = "SELECT x FROM t WHERE x = 10 OR x = 30"
        self.assertNotEqual(structural_signature(adjacent), structural_signature(separated))
        ordinary = "SELECT x FROM t WHERE x <= 5"
        maximum = "SELECT x FROM t WHERE x <= 9223372036854775807"
        self.assertNotEqual(structural_signature(ordinary), structural_signature(maximum))


if __name__ == "__main__":
    unittest.main()

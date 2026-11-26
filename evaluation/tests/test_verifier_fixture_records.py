"""Check independent fixture records and author metadata without a verifier."""

from collections import Counter
import json
from pathlib import Path
import re
import unittest


FIXTURES = Path(__file__).resolve().parents[1] / "verifier_cases.jsonl"
MINIMUM = -(2**63)
MAXIMUM = 2**63 - 1


def declared_columns(schema_sql):
    """Read names in these fixture declarations, without interpreting predicates."""
    return set(re.findall(
        r"(?:\(|,)\s*([A-Za-z_][A-Za-z_0-9]*)\s+BIGINT\s+NOT\s+NULL",
        schema_sql,
        flags=re.IGNORECASE,
    ))


class VerifierFixtureRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = [
            json.loads(line)
            for line in FIXTURES.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        cls.by_id = {record["case_id"]: record for record in cls.records}

    def assert_integer(self, value):
        self.assertIs(type(value), int)
        self.assertLessEqual(MINIMUM, value)
        self.assertLessEqual(value, MAXIMUM)

    def test_exact_record_contract_unique_ids_and_independent_label_counts(self):
        fields = {
            "case_id", "schema_sql", "original_sql", "candidate_sql",
            "expected_status", "justification", "counterexample", "enumeration",
        }
        self.assertEqual(len(self.records), 30)
        self.assertEqual(len(self.by_id), len(self.records))
        self.assertTrue(FIXTURES.read_text(encoding="utf-8").isascii())
        for record in self.records:
            with self.subTest(case_id=record["case_id"]):
                self.assertEqual(set(record), fields)
                for name in fields - {"counterexample", "enumeration"}:
                    self.assertIsInstance(record[name], str)
                    self.assertTrue(record[name].strip())
        self.assertEqual(Counter(record["expected_status"] for record in self.records), {
            "EQUIVALENT": 12, "DIFFERENT": 10, "UNSAT_SCHEMA": 1,
            "INVALID_INPUT": 2, "INVALID_CANDIDATE": 5,
        })

    def test_only_unequal_pairs_have_complete_distinguishing_rows(self):
        fields = {"row", "original_truth", "candidate_truth", "original_rows", "candidate_rows"}
        for record in self.records:
            with self.subTest(case_id=record["case_id"]):
                counterexample = record["counterexample"]
                if record["expected_status"] != "DIFFERENT":
                    self.assertIsNone(counterexample)
                    continue
                self.assertEqual(set(counterexample), fields)
                self.assertEqual(set(counterexample["row"]), declared_columns(record["schema_sql"]))
                for value in counterexample["row"].values():
                    self.assert_integer(value)
                for key in ("original_truth", "candidate_truth"):
                    self.assertIs(type(counterexample[key]), bool)
                self.assertNotEqual(counterexample["original_truth"], counterexample["candidate_truth"])
                for name in ("original_rows", "candidate_rows"):
                    self.assertIsInstance(counterexample[name], list)
                    for row in counterexample[name]:
                        self.assertIsInstance(row, list)
                        self.assertTrue(row)
                        for value in row:
                            self.assert_integer(value)
                original = Counter(map(tuple, counterexample["original_rows"]))
                candidate = Counter(map(tuple, counterexample["candidate_rows"]))
                self.assertNotEqual(original, candidate)

    def test_supported_enumerations_cover_every_column_with_disjoint_maps(self):
        for record in self.records:
            with self.subTest(case_id=record["case_id"]):
                enumeration = record["enumeration"]
                if record["expected_status"].startswith("INVALID_"):
                    self.assertIsNone(enumeration)
                    continue
                self.assertEqual(set(enumeration), {"domains", "fixed_values"})
                domains, fixed = enumeration["domains"], enumeration["fixed_values"]
                self.assertIsInstance(domains, dict)
                self.assertIsInstance(fixed, dict)
                self.assertFalse(set(domains) & set(fixed))
                self.assertEqual(set(domains) | set(fixed), declared_columns(record["schema_sql"]))
                for values in domains.values():
                    self.assertIsInstance(values, list)
                    self.assertTrue(values)
                    self.assertEqual(len(values), len(set(values)))
                    for value in values:
                        self.assert_integer(value)
                for value in fixed.values():
                    self.assert_integer(value)

    def test_e2_schema_removal_and_hand_derived_witnesses_are_preserved(self):
        equivalent = self.by_id["e2-ordering-equivalent"]
        removed = self.by_id["e2-check-removed"]
        wrong = self.by_id["e2-wrong-end-bound"]
        self.assertEqual(equivalent["expected_status"], "EQUIVALENT")
        self.assertEqual(
            equivalent["schema_sql"].replace(", CHECK (start_day <= end_day)", ""),
            removed["schema_sql"],
        )
        self.assertEqual(removed["counterexample"]["row"], {
            "row_id": 1, "start_day": 10, "end_day": 9,
        })
        self.assertEqual(wrong["counterexample"]["row"], {
            "row_id": 1, "start_day": 9, "end_day": 10,
        })
        self.assertEqual(equivalent["enumeration"], removed["enumeration"])

    def test_bounded_agreement_does_not_replace_the_unrestricted_label(self):
        case = self.by_id["bounded-agreement-only"]
        self.assertEqual(case["expected_status"], "DIFFERENT")
        self.assertEqual(case["enumeration"]["domains"], {"x": [-2, -1, 0, 1, 2]})
        self.assertEqual(case["enumeration"]["fixed_values"], {"row_id": 1})
        distinguishing_value = case["counterexample"]["row"]["x"]
        self.assertEqual(distinguishing_value, 3)
        self.assertNotIn(distinguishing_value, case["enumeration"]["domains"]["x"])

    def test_duplicate_projection_has_two_distinct_inputs_with_the_same_value(self):
        case = self.by_id["repeated-projection"]
        self.assertEqual(case["expected_status"], "EQUIVALENT")
        self.assertTrue(case["original_sql"].startswith("SELECT x FROM t WHERE "))
        self.assertTrue(case["candidate_sql"].startswith("SELECT x FROM t WHERE "))
        self.assertEqual(case["enumeration"], {
            "domains": {"row_id": [1, 2]}, "fixed_values": {"x": 7},
        })
        self.assertIn("two copies of [7]", case["justification"])

    def test_ordered_projection_and_changed_table_controls_remain_invalid(self):
        case = self.by_id["multi-column-projection"]
        self.assertEqual(case["expected_status"], "EQUIVALENT")
        for name in ("original_sql", "candidate_sql"):
            self.assertTrue(case[name].startswith("SELECT row_id, x FROM t WHERE "))
        for case_id in ("candidate-projection-change", "candidate-projection-order", "candidate-table-change"):
            with self.subTest(case_id=case_id):
                control = self.by_id[case_id]
                self.assertEqual(control["expected_status"], "INVALID_CANDIDATE")
                self.assertIsNone(control["enumeration"])

    def test_bigint_endpoints_and_adjacent_witnesses_keep_integer_precision(self):
        expected = {
            "bigint-minimum-strict": MINIMUM,
            "bigint-minimum-neighbor": MINIMUM + 1,
            "bigint-maximum-neighbor": MAXIMUM - 1,
            "bigint-maximum-strict": MAXIMUM,
        }
        domain = [MINIMUM, MINIMUM + 1, -1, 0, MAXIMUM - 1, MAXIMUM]
        for case_id, value in expected.items():
            with self.subTest(case_id=case_id):
                case = self.by_id[case_id]
                self.assertEqual(case["counterexample"]["row"]["x"], value)
                self.assertIs(type(case["counterexample"]["row"]["x"]), int)
                self.assertEqual(case["enumeration"]["domains"]["x"], domain)

    def test_false_predicate_and_inconsistent_schema_have_distinct_labels(self):
        false_query = self.by_id["false-query-supported"]
        contradiction = self.by_id["contradictory-checks"]
        self.assertEqual(false_query["expected_status"], "EQUIVALENT")
        self.assertNotIn("CHECK", false_query["schema_sql"])
        self.assertEqual(contradiction["expected_status"], "UNSAT_SCHEMA")
        self.assertIn("CHECK (x < 0)", contradiction["schema_sql"])
        self.assertIn("CHECK (x >= 0)", contradiction["schema_sql"])
        self.assertIsNotNone(false_query["enumeration"])
        self.assertIsNotNone(contradiction["enumeration"])


if __name__ == "__main__":
    unittest.main()

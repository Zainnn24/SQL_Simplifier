"""Test source fingerprints and explicit substitute labels, without real verdicts."""

from contextlib import redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from evaluation.provenance import production_provenance
from evaluation.verifier_check import main


ROOT = Path(__file__).resolve().parents[2]


class ProvenanceTests(unittest.TestCase):
    def test_real_source_bytes_determine_fingerprint_in_an_isolated_source_control(self):
        # This temporary source is a fingerprint control, never production evidence.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "verifier/__init__.py"
            source.parent.mkdir()
            text = (
                "class InputError(ValueError): pass\n"
                "def validate_input(*args, **kwargs): pass\n"
                "def verify(*args, **kwargs): pass\n"
                "def encode_query(*args, **kwargs): pass\n"
            )
            source.write_text(text, encoding="utf-8", newline="\n")
            api = ModuleType("verifier")
            api.__file__ = str(source)
            exec(compile(text, str(source), "exec"), api.__dict__)
            with patch("evaluation.provenance.ROOT", root), patch.dict("sys.modules", {"verifier": api}):
                first = production_provenance(api)
                self.assertTrue(first["verified"], first)
                self.assertEqual(first["files"]["verifier/__init__.py"], hashlib.sha256(source.read_bytes()).hexdigest())
                self.assertEqual(first, production_provenance(api))
                source.write_bytes(source.read_bytes() + b"# Changed source control.\n")
                self.assertNotEqual(first["fingerprint"], production_provenance(api)["fingerprint"])
                api.__benchmark_test_standin__ = True
                self.assertFalse(production_provenance(api)["verified"])

    def test_matching_export_names_in_a_namespace_do_not_become_real_evidence(self):
        api = SimpleNamespace(validate_input=lambda: None, verify=lambda: None,
                              encode_query=lambda: None, InputError=ValueError)
        result = production_provenance(api)
        self.assertFalse(result["verified"])
        self.assertIsNone(result["fingerprint"])
        self.assertTrue(result["diagnostic"])

    def test_experiment_a_cli_labels_an_injected_api_in_its_saved_report(self):
        api = SimpleNamespace(__benchmark_test_standin__=True)
        offline = {"summary": {"failed_cases": 0, "type_bound_probe_errors": 0}, "cases": []}
        with TemporaryDirectory() as directory:
            output = Path(directory) / "test-only-report.json"
            with patch("evaluation.verifier_check.load_production", return_value=api), \
                 patch("evaluation.verifier_check.run_offline", return_value=offline), \
                 patch("evaluation.verifier_check.run_production_suite", return_value={"metrics": {}, "discrepancies": []}), \
                 redirect_stdout(StringIO()):
                status = main(["--fixtures", str(ROOT / "evaluation/verifier_cases.jsonl"), "--output", str(output)])
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(status, 0)
        self.assertTrue(report["test_standin"])
        self.assertFalse(report["verifier_provenance"]["verified"])
        self.assertIsNone(report["verifier_provenance"]["fingerprint"])


if __name__ == "__main__":
    unittest.main()

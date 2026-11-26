import json
import subprocess
import sys
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from LLM_pipeline.config import load_scripted_config
from LLM_pipeline.e2_fixture import dummy_validate_input, dummy_verify
from LLM_pipeline.model import ScriptedModel
from LLM_pipeline.runner import run_task


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class ScriptedE2Test(unittest.TestCase):
    def test_wrong_proposal_is_logged_and_repaired(self):
        config = load_scripted_config(FIXTURES / "e2-scripted-v1.json")
        task = json.loads((FIXTURES / "e2-01.jsonl").read_text(encoding="utf-8"))
        model = ScriptedModel()
        log = StringIO()

        run_task(
            task, config, repeat=1, model=model, validate_input=dummy_validate_input,
            verify=dummy_verify, log=log,
        )

        first, second, terminal = (json.loads(line) for line in log.getvalue().splitlines())
        self.assertEqual([first["record_type"], second["record_type"], terminal["record_type"]],
                         ["attempt", "attempt", "run_end"])
        self.assertEqual(first["check_result"]["status"], "DIFFERENT")
        self.assertEqual(first["check_result"]["witness"]["row"],
                         {"row_id": 2, "start_day": 9, "end_day": 10})
        self.assertIsNone(first["feedback"])
        self.assertEqual(second["check_result"]["status"], "EQUIVALENT")
        self.assertEqual(second["check_result"]["original_nodes"], 7)
        self.assertEqual(second["check_result"]["candidate_nodes"], 3)
        self.assertIn("start_day=9, end_day=10", second["feedback"])
        initial_prompt, retry_prompt = model.prompts
        self.assertEqual(retry_prompt, f"{initial_prompt}\n\n{second['feedback']}")
        self.assertEqual(
            initial_prompt.split("\n\nSchema\n", 1)[1],
            f"{task['schema_sql']}\n\nOriginal query\n{task['original_sql']}",
        )
        self.assertEqual(terminal["preflight_result"]["status"], "READY")
        self.assertEqual(terminal["terminal_reason"], "ACCEPTED")
        self.assertTrue(terminal["accepted"])
        self.assertEqual(terminal["calls_used"], 2)
        self.assertEqual(terminal["selected_sql"], task["reference_sql"])

    def test_copy_first_cli_retries_an_equivalent_query_of_equal_size(self):
        manifest = FIXTURES / "e2-01.jsonl"
        task = json.loads(manifest.read_text(encoding="utf-8"))

        with TemporaryDirectory() as directory:
            result = subprocess.run(
                [
                    sys.executable, "-B", "-m", "LLM_pipeline", "run",
                    "--manifest", str(manifest), "--task", "E2-01", "--repeat", "1",
                    "--config", str(FIXTURES / "e2-copy-first-v1.json"), "--output", directory,
                ],
                cwd=FIXTURES.parents[1], capture_output=True, text=True,
                encoding="utf-8",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            trace_path = Path(directory) / "e2-copy-first-v1-E2-01-r1.jsonl"
            lines = trace_path.read_text(encoding="utf-8").splitlines()

        first, second, terminal = map(json.loads, lines)
        self.assertEqual(first["candidate_sql"], task["original_sql"])
        self.assertEqual(first["check_result"]["status"], "EQUIVALENT")
        self.assertEqual(first["check_result"]["original_nodes"], 7)
        self.assertEqual(first["check_result"]["candidate_nodes"], 7)
        self.assertIn("strictly smaller", second["feedback"])
        self.assertEqual(second["candidate_sql"], task["reference_sql"])
        self.assertEqual(second["check_result"]["candidate_nodes"], 3)
        self.assertEqual(terminal["terminal_reason"], "ACCEPTED")
        self.assertEqual(terminal["calls_used"], 2)

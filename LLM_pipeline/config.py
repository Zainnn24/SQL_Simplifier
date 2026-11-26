import json
import re
from pathlib import Path

from .model import E2_COPY_FIRST_MODEL_ID, E2_SCRIPTED_MODEL_ID


def load_scripted_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config["model_id"] not in (E2_SCRIPTED_MODEL_ID, E2_COPY_FIRST_MODEL_ID):
        raise ValueError("Select a scripted E2 model.")
    if (config["run_kind"], config["prompt_version"], config["output_parser"]) != (
        "scripted", "generic-v1", "sql-or-single-fence-v1"
    ):
        raise ValueError("This runner entry point expects the scripted run, prompt and parser.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", config["config_id"]):
        raise ValueError("The config ID must be a filename-safe identifier.")
    if type(config["solver_timeout_ms"]) is not int or config["solver_timeout_ms"] <= 0:
        raise ValueError("solver_timeout_ms must be a positive integer.")
    if (
        type(config["max_model_calls"]) is not int
        or config["max_model_calls"] != 4
        or config["transport_policy"] != "no_automatic_retry"
    ):
        raise ValueError("The runner expects a four-call budget and no automatic retries.")
    return config

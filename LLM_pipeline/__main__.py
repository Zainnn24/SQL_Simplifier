import argparse
import json
from pathlib import Path

from .config import load_scripted_config
from .e2_fixture import dummy_validate_input, dummy_verify
from .model import E2_COPY_FIRST_MODEL_ID, ScriptedModel
from .runner import run_task


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the scripted E2-01 development task.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run one task and write its JSONL trace.")
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--task", choices=["E2-01"], required=True)
    run.add_argument("--repeat", type=int, default=1)
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True, help="Directory for the new trace.")
    args = parser.parse_args()
    if args.repeat < 1:
        run.error("--repeat must be a positive integer")

    try:
        config = load_scripted_config(args.config)
        tasks = [
            json.loads(line)
            for line in args.manifest.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        matches = [task for task in tasks if task["task_id"] == args.task]
        if len(matches) != 1 or matches[0]["split"] != "dev":
            raise ValueError("The manifest must contain one E2-01 development task.")

        task = matches[0]
        model_id = config["model_id"]
        if model_id == E2_COPY_FIRST_MODEL_ID:
            model = ScriptedModel((task["original_sql"], task["reference_sql"]), model_id=model_id)
        else:
            model = ScriptedModel()

        args.output.mkdir(parents=True, exist_ok=True)
        trace_path = args.output / f"{config['config_id']}-{args.task}-r{args.repeat}.jsonl"
        with trace_path.open("x", encoding="utf-8", newline="\n") as log:
            summary = run_task(
                task, config, repeat=args.repeat, model=model,
                validate_input=dummy_validate_input, verify=dummy_verify, log=log,
            )
    except FileExistsError:
        run.error("the trace path already exists; use a fresh --output directory")
    except (OSError, ValueError, KeyError, TypeError) as error:
        run.error(str(error))

    print(f"{args.task} {summary['terminal_reason']} after {summary['calls_used']} calls")
    print(trace_path)
    return 0 if summary["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

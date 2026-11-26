"""Dispatch benchmark intake, sample checks, Experiment A and offline scoring."""

import argparse
import sys


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "check-samples":
        from .sample_check import main as samples_main
        return samples_main(args)
    if args and args[0] == "check-verifier":
        from .verifier_check import main as verifier_main
        return verifier_main(args[1:])
    if args and args[0] == "score":
        from .scorer import main as score_main
        return score_main(args[1:])
    if args and args[0] == "validate-benchmark":
        from .benchmark_check import main as benchmark_main
        return benchmark_main(args[1:])
    parser = argparse.ArgumentParser(description="SQL benchmark and Experiment A evaluation utilities")
    parser.add_argument("command", choices=("check-samples", "check-verifier", "score", "validate-benchmark"))
    parser.parse_args(args)
    return 0

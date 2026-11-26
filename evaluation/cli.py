"""Dispatch sample checks and independent Experiment A checks."""

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
    parser = argparse.ArgumentParser(description="SQL benchmark and Experiment A evaluation utilities")
    parser.add_argument("command", choices=("check-samples", "check-verifier"))
    parser.parse_args(args)
    return 0

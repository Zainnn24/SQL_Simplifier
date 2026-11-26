"""Reproduce Martin's offline author checks without a model or verifier."""

from collections import Counter, defaultdict
from itertools import permutations
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

import duckdb

from .sample_check import DEFAULT_SEEDS, ROOT, check_task, format_check, load_task, main as sample_main


FAMILIES = ("B3", "B4", "B5", "C1", "C2", "C3", "C4", "C5")
TASK_IDS = tuple(f"{family}-{variant:02d}" for family in FAMILIES for variant in range(1, 5))
NEW_FAMILIES = ("B4", "B5", "C2", "C3", "C4", "C5")
FIXTURES = ("b4_b5.json", "c2_c3.json", "c4_c5.json")
DEV_NODES = {
    "B3-01": (7, 3), "B3-02": (8, 3), "B3-03": (15, 11), "B3-04": (11, 7),
    "C1-01": (19, 11), "C1-02": (23, 7), "C1-03": (23, 15), "C1-04": (23, 15),
}
TOKEN = re.compile(r'-?\d+|[A-Za-z_][A-Za-z_0-9]*|!=|<=|>=|[=<>(),;]')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def task_path(task_id):
    return ROOT / "evaluation" / f"{task_id}.json"


def load_tasks():
    return {task_id: load_task(task_path(task_id)) for task_id in TASK_IDS}


def load_evidence():
    evidence = {
        task_id: {
            "task_id": task_id,
            "author_nodes": {"original": counts[0], "reference": counts[1]},
            "production_verifier_status": "PENDING_EXPERIMENT_A",
            "wrong_references": [],
        }
        for task_id, counts in DEV_NODES.items()
    }
    for filename in FIXTURES:
        records = json.loads((ROOT / "evaluation/tests/fixtures" / filename).read_text(encoding="utf-8"))
        for record in records:
            task_id = record["task_id"]
            require(task_id not in evidence, f"Duplicate author evidence: {task_id}")
            evidence[task_id] = record
    require(set(evidence) == set(TASK_IDS), "Author evidence must cover exactly Martin's 32 tasks")
    return evidence


def audit_structure(tasks, evidence):
    required = {
        "task_id", "family", "split", "schema_sql", "original_sql", "reference_sql",
        "sample_rows", "expected_rows", "derivation", "source", "error_exposed",
    }
    require(set(tasks) == set(TASK_IDS), "Expected exactly the 32 assigned task paths")
    require(len({task["task_id"] for task in tasks.values()}) == 32, "Task IDs are not unique")
    for task_id, task in tasks.items():
        require(required.issubset(task), f"{task_id}: incomplete base record")
        require(all(task[key] is not None for key in required), f"{task_id}: null base field")
        require(task["task_id"] == task_id, f"{task_id}: filename/ID mismatch")
        family = task_id.split("-")[0]
        require(task["family"] == family, f"{task_id}: family mismatch")
        require(task["split"] == ("dev" if family in ("B3", "C1") else "test"), f"{task_id}: split mismatch")
        for key in ("derivation", "source", "error_exposed"):
            require(isinstance(task[key], str) and task[key].strip(), f"{task_id}: missing {key}")
        case = evidence[task_id]
        require(case["production_verifier_status"] == "PENDING_EXPERIMENT_A", f"{task_id}: unexpected verifier claim")
        if family in NEW_FAMILIES:
            require(Counter(map(tuple, case["expected_rows"])) == Counter(map(tuple, task["expected_rows"])), f"{task_id}: author expectation mismatch")
            require(bool(case["wrong_references"]) == (not task_id.endswith("-01")), f"{task_id}: wrong-reference coverage mismatch")
    reviews = [json.loads(line) for line in (ROOT / "evaluation/benchmark_reviews.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    counts = Counter(record["task_id"] for record in reviews)
    for task_id in TASK_IDS:
        require(counts[task_id] == 1, f"{task_id}: expected exactly one review record")
    selected = [review for review in reviews if review["task_id"] in tasks]
    for review in selected:
        require(review["author_role"] == "evaluation" and review["reviewer_role"] == "llm", f"{review['task_id']}: review assignment mismatch")
        require(review["review_status"] in ("PENDING", "APPROVED"), "Unknown review status")
        notes = review["review_notes"]
        require(notes is None or isinstance(notes, str), "Review notes must be text or null")
        if review["review_status"] == "APPROVED":
            require(isinstance(notes, str) and notes.strip(), "Approval requires nonempty notes")
        require(review["check_removal_test"] is None, "No constraint-removal test is needed for these families")
    return Counter(review["review_status"] for review in selected)


def sql_tokens(sql):
    """Tokenize these integer-only fixtures for author counting and seed fidelity."""
    sql = re.sub(r"--[^\n]*", "", sql)
    tokens = []
    end = 0
    for match in TOKEN.finditer(sql):
        require(not sql[end:match.start()].strip(), "Unexpected token in author fixture SQL")
        tokens.append(match[0].lower())
        end = match.end()
    require(not sql[end:].strip(), "Unexpected trailing token in author fixture SQL")
    return tuple(token for token in tokens if token != ";")


def author_node_count(sql):
    tokens = sql_tokens(sql)
    require("where" in tokens, "An explicit WHERE predicate is required")
    return sum(token not in ("(", ")") for token in tokens[tokens.index("where") + 1:])


def check_seed_sql(tasks):
    """Compare CREATE and both SELECTs with seed text, ignoring formatting only."""
    text = DEFAULT_SEEDS.read_text(encoding="utf-8")
    headings = list(re.finditer(r"^--\s+([A-E][1-5])\.\s+.*$", text, re.MULTILINE))
    blocks = {
        heading[1]: text[heading.start():headings[index + 1].start() if index + 1 < len(headings) else len(text)]
        for index, heading in enumerate(headings)
    }
    with duckdb.connect(":memory:") as connection:
        for family in FAMILIES:
            statements = connection.extract_statements(blocks[family])
            ddl = [statement.query for statement in statements if statement.type.name == "CREATE"]
            queries = [statement.query for statement in statements if statement.type.name == "SELECT"]
            require(len(ddl) == 1 and len(queries) == 2, f"{family}: unexpected seed structure")
            task = tasks[f"{family}-01"]
            for field, seed in zip(("schema_sql", "original_sql", "reference_sql"), ddl + queries):
                require(sql_tokens(task[field]) == sql_tokens(seed), f"{family}-01: seed {field} differs")
    return len(FAMILIES)


def structural_signature(sql):
    """Screen for cosmetic duplicates using DuckDB's parsed predicate, not SMT.

    Normalize column names, commuted Boolean operands, comparison operand swaps,
    and literal ranks. Preserve adjacency and BIGINT endpoint roles so different
    boundary relationships are not erased. This is a screening heuristic.
    """
    with duckdb.connect(":memory:") as connection:
        parsed = json.loads(connection.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()[0])
    require(not parsed["error"], "DuckDB could not parse structural-screen input")
    predicate = parsed["statements"][0]["node"]["where_clause"]
    columns, literals = set(), set()

    def collect(node):
        if node["class"] == "COLUMN_REF":
            columns.add(tuple(node["column_names"]))
        elif node["class"] == "CONSTANT" and node["literal"]["kind"] == "INTEGER":
            literals.add(int(node["literal"]["text"]))
        for key in ("left", "right"):
            if key in node:
                collect(node[key])
        for child in node.get("children", []):
            collect(child)

    collect(predicate)
    ordered = sorted(literals)
    ranks = {value: index for index, value in enumerate(ordered)}
    roles = ["MIN" if value == -(2**63) else "MAX" if value == 2**63 - 1 else "ordinary" for value in ordered]
    adjacency = [right - left == 1 for left, right in zip(ordered, ordered[1:])]
    inverse = {
        "COMPARE_EQUAL": "COMPARE_EQUAL", "COMPARE_NOTEQUAL": "COMPARE_NOTEQUAL",
        "COMPARE_LESSTHAN": "COMPARE_GREATERTHAN", "COMPARE_GREATERTHAN": "COMPARE_LESSTHAN",
        "COMPARE_LESSTHANOREQUALTO": "COMPARE_GREATERTHANOREQUALTO",
        "COMPARE_GREATERTHANOREQUALTO": "COMPARE_LESSTHANOREQUALTO",
    }

    def canonical(node, names):
        kind = node["class"]
        if kind == "COLUMN_REF":
            return ("column", names[tuple(node["column_names"])])
        if kind == "CONSTANT":
            literal = node["literal"]
            return ("integer", ranks[int(literal["text"])]) if literal["kind"] == "INTEGER" else (literal["kind"], literal["text"])
        if kind == "COMPARISON":
            left, right = canonical(node["left"], names), canonical(node["right"], names)
            direct = (node["type"], left, right)
            swapped = (inverse[node["type"]], right, left)
            return min(direct, swapped)
        children = [canonical(child, names) for child in node.get("children", [])]
        if kind == "CONJUNCTION":
            children.sort()
        require(kind in ("CONJUNCTION", "OPERATOR"), f"Unexpected structural-screen expression: {kind}")
        return (node["type"], tuple(children))

    candidates = []
    for ordering in permutations(sorted(columns)):
        names = {name: index for index, name in enumerate(ordering)}
        candidates.append(json.dumps((roles, adjacency, canonical(predicate, names))))
    return min(candidates)


def duplicate_groups(tasks):
    groups = defaultdict(list)
    for task_id, task in tasks.items():
        groups[structural_signature(task["original_sql"])].append(task_id)
    return [ids for ids in groups.values() if len(ids) > 1]


def check_wrong_reference(task_id, wrong):
    """Run one wrong candidate on a temporary JSON copy and return observations."""
    path = task_path(task_id)
    before = path.read_bytes()
    task = load_task(path)
    task["reference_sql"] = wrong["sql"]
    with TemporaryDirectory() as directory:
        copy = Path(directory) / f"{task_id}.json"
        copy.write_text(json.dumps(task), encoding="utf-8")
        results = check_task(load_task(copy))
    require(path.read_bytes() == before, f"{task_id}: source record changed")
    require(len(results) == 2, f"{task_id}: unexpected negative check structure")
    original, reference = results
    require(original.passed, format_check(original))
    require(not reference.passed and reference.error is None, f"{task_id}: wrong reference was not detected as a row mismatch")
    require(reference.missing == Counter(map(tuple, wrong["missing_rows"])), f"{task_id}: unexpected missing rows: {format_check(reference)}")
    require(reference.unexpected == Counter(map(tuple, wrong["unexpected_rows"])), f"{task_id}: unexpected extra rows: {format_check(reference)}")
    return results


def main():
    print("Offline author checks only; independent review and production verification remain pending.")
    tasks, evidence = load_tasks(), load_evidence()
    statuses = audit_structure(tasks, evidence)
    print(f"STRUCTURE PASS: 32 unique tasks, 8 families x 4 tasks, dev=8, test=24; reviews={dict(statuses)}")
    for task_id in TASK_IDS:
        task, record = tasks[task_id], evidence[task_id]
        counts = (author_node_count(task["original_sql"]), author_node_count(task["reference_sql"]))
        expected = record["author_nodes"]
        require(counts == (expected["original"], expected["reference"]) and counts[0] > counts[1], f"{task_id}: author node-count mismatch")
        print(f"AUTHOR NODES {task_id}: {counts[0]} -> {counts[1]}; production_verifier=PENDING_EXPERIMENT_A")
    print(f"SEED SQL PASS: {check_seed_sql(tasks)} faithful CREATE/original/reference triples")
    duplicates = duplicate_groups(tasks)
    require(not duplicates, f"Potential cosmetic duplicates require investigation: {duplicates}")
    print("STRUCTURAL SCREEN PASS: no normalized duplicate original predicates among these 32 tasks")
    seed_paths = [str(task_path(f"{family}-01")) for family in FAMILIES]
    require(sample_main(["check-samples", "--tasks", *seed_paths, "--seed-file", str(DEFAULT_SEEDS)]) == 0, "Seed sample checks failed")
    all_paths = [str(task_path(task_id)) for task_id in TASK_IDS]
    require(sample_main(["check-samples", "--tasks", *all_paths]) == 0, "Personal task sample checks failed")
    variants = cases = 0
    for task_id in TASK_IDS:
        wrongs = evidence[task_id]["wrong_references"]
        variants += bool(wrongs)
        for wrong in wrongs:
            original, reference = check_wrong_reference(task_id, wrong)
            print(f"EXPECTED NEGATIVE CAPTURED {task_id}: {wrong['sql']}")
            print(format_check(original))
            print(format_check(reference))
            cases += 1
    require(variants == 18, "Expected wrong-reference coverage for exactly 18 new variants")
    print(f"AUTHOR SUMMARY PASS: seeds=24/24; queries=64/64; variants={variants}/18; wrong_references={cases}/{cases}; smaller=32/32")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

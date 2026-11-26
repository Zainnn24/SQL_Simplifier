from pathlib import Path


_TEMPLATE = Path(__file__).with_name("prompts") / "generic-v1.txt"


def build_prompt(schema_sql: str, original_sql: str) -> str:
    return _TEMPLATE.read_text(encoding="utf-8").format(
        schema_sql=schema_sql, original_sql=original_sql
    ).strip()


def extract_candidate(raw_response: str) -> str | None:
    text = raw_response.strip()
    if text.startswith("```"):
        opening, *body = text.splitlines()
        if opening.strip().lower() not in {"```", "```sql"} or not body:
            return None
        *sql, closing = body
        if closing.strip() != "```" or any(line.lstrip().startswith("```") for line in sql):
            return None
        text = "\n".join(sql).strip()
    return text or None

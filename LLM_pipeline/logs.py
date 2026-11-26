import json
from typing import TextIO


def write_record(log: TextIO, identity: dict, record_type: str, **fields) -> None:
    record = {"record_type": record_type, **identity, **fields}
    log.write(json.dumps(record, allow_nan=False) + "\n")
    log.flush()

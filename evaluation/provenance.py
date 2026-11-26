"""Fingerprint the actual verifier implementation used by an experiment."""

import hashlib
import inspect
import json
from pathlib import Path
from types import ModuleType

from .sample_check import ROOT


PUBLIC_EXPORTS = ("validate_input", "verify", "encode_query", "InputError")


def production_provenance(api):
    """Return source evidence, explicitly unverified for injected test doubles.

    Every public export must originate in the project's verifier directory. A
    namespace containing plans, a Mock, or a scripted API cannot acquire a real
    production fingerprint merely by supplying matching attribute names.
    """
    result = {"verified": False, "module": "verifier", "files": {}, "exports": {},
              "fingerprint": None, "diagnostic": None}
    try:
        if not isinstance(api, ModuleType) or api.__name__ != "verifier":
            raise ValueError("The API is not the imported production verifier module")
        if getattr(api, "__benchmark_test_standin__", False):
            raise ValueError("Explicit test stand-ins do not have production provenance")
        directory = (ROOT / "verifier").resolve()
        files = sorted(path for path in directory.rglob("*.py") if "__pycache__" not in path.parts)
        if not files:
            raise ValueError("No production verifier Python source files exist")
        for path in files:
            resolved = path.resolve()
            if not resolved.is_relative_to(directory):
                raise ValueError("Verifier source resolves outside the project verifier directory")
            result["files"][resolved.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        for name in PUBLIC_EXPORTS:
            value = getattr(api, name, None)
            if name == "InputError":
                if not isinstance(value, type) or not issubclass(value, ValueError):
                    raise ValueError("Production InputError must be a ValueError subclass")
            elif not inspect.isfunction(value):
                raise ValueError(f"Production {name} must be a source-backed function")
            module = getattr(value, "__module__", "")
            if module != "verifier" and not module.startswith("verifier."):
                raise ValueError(f"Production {name} is defined outside the verifier package")
            filename = inspect.getsourcefile(value)
            if filename is None:
                raise ValueError(f"Production {name} has no inspectable source file")
            path = Path(filename).resolve()
            if not path.is_relative_to(directory) or path.relative_to(ROOT).as_posix() not in result["files"]:
                raise ValueError(f"Production {name} source is outside the project verifier directory")
            result["exports"][name] = {"module": module, "qualname": value.__qualname__,
                                       "file": path.relative_to(ROOT).as_posix()}
        payload = {key: result[key] for key in ("module", "files", "exports")}
        result["fingerprint"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        result["verified"] = True
    except Exception as error:
        result["diagnostic"] = f"{type(error).__name__}: {error}"
    return result

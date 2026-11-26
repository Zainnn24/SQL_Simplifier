"""Require the verifier team's public implementation without substitutes."""

from importlib import import_module
from types import ModuleType


class ProductionUnavailable(RuntimeError):
    """The production verifier cannot provide a required public interface."""

    def __init__(self, message: str, *, missing=()):
        super().__init__(message)
        self.missing = tuple(missing)


def load_production(
    required: tuple[str, ...] = ("validate_input", "verify", "encode_query", "InputError"),
) -> ModuleType:
    """Import the real verifier and validate only the requested public exports.

    A directory containing plans alone is not an implemented verifier. Missing
    dependencies and incomplete interfaces must remain visible to callers.
    """
    try:
        module = import_module("verifier")
    except Exception as error:
        raise ProductionUnavailable(
            f"Cannot import production verifier: {type(error).__name__}: {error}",
            missing=required,
        ) from error

    unavailable, missing_names = [], []
    for name in required:
        value = getattr(module, name, None)
        if name == "InputError":
            if not isinstance(value, type) or not issubclass(value, ValueError):
                unavailable.append("InputError (must be a ValueError subclass)")
                missing_names.append(name)
        elif not callable(value):
            unavailable.append(f"{name} (must be callable)")
            missing_names.append(name)
    if unavailable:
        raise ProductionUnavailable(
            "Production verifier is missing required interfaces: " + ", ".join(unavailable),
            missing=missing_names,
        )
    return module

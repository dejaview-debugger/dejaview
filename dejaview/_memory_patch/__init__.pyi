# keep this file in sync with src/lib.rs

from typing import Any

__all__ = [
    "deterministic_id",
    "disable",
    "enable",
]

def deterministic_id(obj: Any) -> int:
    """
    Get a deterministic ID for a Python object.
    Only works after the patch is enabled.
    """
    ...

def enable() -> None:
    """
    Enable the patch. After this, `id()` and `hash()` will return
    deterministic values based on access order.
    Raises RuntimeError if the patch is already enabled.
    """
    ...

def disable() -> None:
    """
    Disable the patch and restore original behavior.
    Raises RuntimeError if the patch is not currently enabled.
    """
    ...

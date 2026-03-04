"""Custom patchers for functions that return complex stateful objects.

These patchers extend the base :class:`Patcher` protocol for cases where
simple memoization (:class:`GenericPatcher`) is insufficient — typically
functions that return file-like objects, network responses, or process
handles.
"""

from __future__ import annotations

import io
from typing import Any, Callable

from dejaview.patching.patcher import Patcher

# ---------------------------------------------------------------------------
# Filesystem – tempfile
# ---------------------------------------------------------------------------


class TempFilePatcher(Patcher[Any, tuple]):
    """Patcher for ``tempfile.NamedTemporaryFile`` / ``TemporaryFile``.

    During play the real temporary file is created (side-effects happen).
    During replay an in-memory buffer is returned instead so that no files
    are created on disk.
    """

    @staticmethod
    def play(func: Callable, *args: Any, **kwargs: Any):  # noqa: ANN205
        tmp = func(*args, **kwargs)
        mode = kwargs.get("mode", "w+b")
        binary = "b" in mode
        name: str = getattr(tmp, "name", "<tempfile>")
        state = (binary, name)
        return (lambda: tmp), state

    @staticmethod
    def replay(func: Callable, state: tuple, *args: Any, **kwargs: Any) -> Any:
        binary, name = state
        buf: Any = io.BytesIO() if binary else io.StringIO()
        buf.name = name  # type: ignore[attr-defined]
        return buf


class _ReplayTemporaryDirectory:
    """Lightweight stand-in for ``tempfile.TemporaryDirectory`` during replay."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> str:
        return self.name

    def __exit__(self, *args: Any) -> None:
        pass

    def cleanup(self) -> None:
        pass


class TempDirPatcher(Patcher[Any, tuple]):
    """Patcher for ``tempfile.TemporaryDirectory``.

    During play the real temporary directory is created (side-effects happen).
    During replay a lightweight stand-in is returned that yields the same
    directory name without creating anything on disk.
    """

    @staticmethod
    def play(func: Callable, *args: Any, **kwargs: Any):  # noqa: ANN205
        tmp_dir = func(*args, **kwargs)
        name: str = tmp_dir.name
        state = (name,)
        return (lambda: tmp_dir), state

    @staticmethod
    def replay(func: Callable, state: tuple, *args: Any, **kwargs: Any) -> Any:
        (name,) = state
        return _ReplayTemporaryDirectory(name)

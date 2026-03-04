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
# Sub-processes
# ---------------------------------------------------------------------------


class _ReplayPopen:
    """Lightweight stand-in for ``subprocess.Popen`` during replay.

    Stores captured *stdout*, *stderr*, and *returncode* so that the
    common ``proc.communicate()`` / ``proc.wait()`` patterns work.
    """

    def __init__(
        self,
        stdout: bytes | None,
        stderr: bytes | None,
        returncode: int,
        pid: int = -1,
        args: Any = None,
    ) -> None:
        self._stdout_data = stdout
        self._stderr_data = stderr
        self.returncode = returncode
        self.pid = pid
        self.args = args or []
        self.stdin = None
        self.stdout = io.BytesIO(stdout) if stdout is not None else None
        self.stderr = io.BytesIO(stderr) if stderr is not None else None

    def communicate(
        self,
        input: bytes | None = None,  # noqa: A002
        timeout: float | None = None,
    ) -> tuple[bytes | None, bytes | None]:
        return self._stdout_data, self._stderr_data

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def poll(self) -> int:
        return self.returncode

    def kill(self) -> None:
        pass

    def terminate(self) -> None:
        pass

    def __enter__(self) -> _ReplayPopen:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class PopenPatcher(Patcher[Any, tuple]):
    """Patcher for ``subprocess.Popen``.

    During play the process is started and eagerly run to completion via
    ``communicate()`` so that *stdout*, *stderr*, and *returncode* can be
    captured.  A lightweight :class:`_ReplayPopen` is returned to the
    caller (and again during replay).
    """

    @staticmethod
    def play(func: Callable, *args: Any, **kwargs: Any):  # noqa: ANN205
        proc = func(*args, **kwargs)
        stdout, stderr = proc.communicate()
        returncode = proc.returncode
        pid = proc.pid
        proc_args = proc.args
        state = (stdout, stderr, returncode, pid, proc_args)

        def run() -> Any:
            return _ReplayPopen(stdout, stderr, returncode, pid, proc_args)

        return run, state

    @staticmethod
    def replay(func: Callable, state: tuple, *args: Any, **kwargs: Any) -> Any:
        stdout, stderr, returncode, pid, proc_args = state
        return _ReplayPopen(stdout, stderr, returncode, pid, proc_args)

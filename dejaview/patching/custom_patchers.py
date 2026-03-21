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
import urllib.response
from typing import Any, Callable

from dejaview.patching.patcher import GenericPatcher, GenericPatcherState, Patcher
from dejaview.patching.util import hide_from_traceback

# ---------------------------------------------------------------------------
# Networking – urllib
# ---------------------------------------------------------------------------
#
# We patch ``urlopen`` rather than relying on socket-level patches because
# HTTPS uses ``ssl.SSLSocket`` which performs read/write through a C-level
# ``_sslobj`` — this bypasses our patched ``socket.recv``/``socket.send``
# entirely, so socket patches alone cannot replay HTTPS traffic.
#
# We intentionally skip:
# - ``OpenerDirector.open``: internal dispatch called by ``urlopen``; patching
#   ``urlopen`` at the top level already covers all calls that go through it.
# - ``URLopener`` / ``FancyURLopener``: deprecated since Python 3.3 and
#   removed in 3.12.
# ---------------------------------------------------------------------------


class UrlopenPatcher(Patcher[Any, tuple]):
    """Patcher for ``urllib.request.urlopen``.

    During play the URL is fetched and the full response body is read.
    Both play and replay return a real :class:`urllib.response.addinfourl`
    wrapping a :class:`io.BytesIO` of the captured data, so ``isinstance``
    checks, ``readinto``, and non-HTTP URL schemes (``file:``, ``ftp:``,
    ``data:``) all work transparently.
    """

    @staticmethod
    def _make_addinfourl(
        data: bytes, headers: Any, url: str, status: int
    ) -> urllib.response.addinfourl:
        return urllib.response.addinfourl(io.BytesIO(data), headers, url, status)

    @staticmethod
    def play(func: Callable, *args: Any, **kwargs: Any):  # noqa: ANN205
        # Delegate exception capture to GenericPatcher
        run, state = GenericPatcher.play(func, *args, **kwargs)
        if state.exc_info is not None:
            return run, state

        resp = state.return_value
        data: bytes = resp.read()
        headers = resp.info()
        url: str = resp.geturl()
        status: int = getattr(resp, "status", getattr(resp, "code", 200))
        resp.close()
        urlopen_state = (data, headers, url, status)

        def run_ok() -> Any:
            return UrlopenPatcher._make_addinfourl(data, headers, url, status)

        return run_ok, urlopen_state

    @staticmethod
    @hide_from_traceback
    def replay(func: Callable, state: Any, *args: Any, **kwargs: Any) -> Any:
        # GenericPatcherState means an exception was captured during play
        if isinstance(state, GenericPatcherState):
            return GenericPatcher.replay(func, state, *args, **kwargs)

        data, headers, url, status = state
        return UrlopenPatcher._make_addinfourl(data, headers, url, status)

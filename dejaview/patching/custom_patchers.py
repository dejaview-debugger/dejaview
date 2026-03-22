"""Custom patchers for functions that return complex stateful objects.

These patchers extend the base :class:`Patcher` protocol for cases where
simple memoization (:class:`GenericPatcher`) is insufficient.
"""

from __future__ import annotations

import io
import socket
import urllib.response
from typing import Any, Callable

from dejaview.patching.patcher import GenericPatcher, GenericPatcherState, Patcher
from dejaview.patching.util import hide_from_traceback


def _is_not_af_unix(self: socket.socket, *args: Any, **kwargs: Any) -> bool:
    """Return True when a socket should be patched (i.e. is not AF_UNIX)."""
    try:
        return self.family != socket.AF_UNIX
    except Exception:  # noqa: BLE001
        return True


def _is_af_unix_from_init_args(*args: Any, **kwargs: Any) -> bool:
    """Check if __init__ args specify AF_UNIX."""
    # __init__(self, family=AF_INET, type=SOCK_STREAM, proto=0, fileno=None)
    # args[0] is self
    family = kwargs.get("family", socket.AF_INET)
    if len(args) > 1:
        family = args[1]
    return family == socket.AF_UNIX


class SocketInitPatcher(Patcher[Any, Any]):
    """Patcher for ``socket.socket.__init__``.

    On play, the real ``__init__`` runs and any exception is captured.
    On replay, the real ``__init__`` is called again (to create a valid
    C-level socket) — family/type/proto are determined by the arguments
    which are the same during replay. All subsequent socket methods are
    memoized by ``GenericPatcher`` (via ``should_patch``).

    AF_UNIX sockets are never patched — they pass through directly.
    """

    @staticmethod
    def play(func: Callable, *args: Any, **kwargs: Any):  # noqa: ANN205
        if _is_af_unix_from_init_args(*args, **kwargs):
            func(*args, **kwargs)
            return (lambda: None), None

        return GenericPatcher.play(func, *args, **kwargs)

    @staticmethod
    @hide_from_traceback
    def replay(func: Callable, state: Any, *args: Any, **kwargs: Any) -> Any:
        if state is None:
            # AF_UNIX — pass through
            return func(*args, **kwargs)

        # Re-raise captured exception if __init__ failed during play
        if isinstance(state, GenericPatcherState) and state.exc_info is not None:
            return GenericPatcher.replay(func, state, *args, **kwargs)

        # Create a real socket — family/type/proto come from the args
        return func(*args, **kwargs)


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
        stdout: bytes | str | None,
        stderr: bytes | str | None,
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
        self.stdout = self._wrap_stream(stdout)
        self.stderr = self._wrap_stream(stderr)

    @staticmethod
    def _wrap_stream(
        data: bytes | str | None,
    ) -> io.BytesIO | io.StringIO | None:
        if isinstance(data, str):
            return io.StringIO(data)
        if data is not None:
            return io.BytesIO(data)
        return None

    def communicate(
        self,
        input: bytes | None = None,  # noqa: A002
        timeout: float | None = None,
    ) -> tuple[bytes | str | None, bytes | str | None]:
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

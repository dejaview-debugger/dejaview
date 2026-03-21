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


def _is_af_unix(self: socket.socket, *args: Any, **kwargs: Any) -> bool:
    """Check if a socket instance is AF_UNIX."""
    try:
        return self.family == socket.AF_UNIX
    except Exception:  # noqa: BLE001
        return False


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
    memoized by ``SocketMethodPatcher``.

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


class SocketMethodPatcher(Patcher[Any, Any]):
    """Patcher for socket instance methods (bind, recv, send, etc.).

    Skips patching for AF_UNIX sockets. Otherwise delegates to
    GenericPatcher.
    """

    @staticmethod
    def play(func: Callable, *args: Any, **kwargs: Any):  # noqa: ANN205
        # args[0] is self for instance methods
        if args and isinstance(args[0], socket.socket) and _is_af_unix(args[0]):
            passthrough = func(*args, **kwargs)
            return (lambda: passthrough), None

        return GenericPatcher.play(func, *args, **kwargs)

    @staticmethod
    @hide_from_traceback
    def replay(func: Callable, state: Any, *args: Any, **kwargs: Any) -> Any:
        if state is None:
            # AF_UNIX — pass through
            return func(*args, **kwargs)

        return GenericPatcher.replay(func, state, *args, **kwargs)


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

"""Custom patchers for functions that return complex stateful objects.

These patchers extend the base :class:`Patcher` protocol for cases where
simple memoization (:class:`GenericPatcher`) is insufficient — typically
functions that return file-like objects, network responses, or process
handles.
"""

from __future__ import annotations

import io
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

"""Custom patchers for functions that return complex stateful objects.

These patchers extend the base :class:`Patcher` protocol for cases where
simple memoization (:class:`GenericPatcher`) is insufficient — typically
functions that return file-like objects, network responses, or process
handles.
"""

from __future__ import annotations

import io
import sys
import urllib.response
from typing import Any, Callable

import tblib  # type: ignore[import-untyped]
import tblib.pickling_support  # type: ignore[import-untyped]

from dejaview.patching.patcher import ExcInfo, Patcher
from dejaview.patching.util import hide_from_traceback

# ---------------------------------------------------------------------------
# Networking – urllib
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
    def play(func: Callable, *args: Any, **kwargs: Any):  # noqa: ANN205
        exc_info: ExcInfo | None = None
        try:
            resp = func(*args, **kwargs)
        except BaseException as err:
            tblib.pickling_support.install(err)
            _, ev, tb = sys.exc_info()
            assert ev is not None
            exc_info = ExcInfo(e=ev, tb=tblib.Traceback(tb))

        if exc_info is not None:
            captured = exc_info

            @hide_from_traceback
            def run_err() -> Any:
                tb = captured.tb.as_traceback().tb_next
                raise captured.e.with_traceback(tb)

            return run_err, captured

        data: bytes = resp.read()
        headers = resp.info()
        url: str = resp.geturl()
        status: int = getattr(resp, "status", getattr(resp, "code", 200))
        resp.close()
        state = (data, headers, url, status)

        def run() -> Any:
            return urllib.response.addinfourl(io.BytesIO(data), headers, url, status)

        return run, state

    @staticmethod
    @hide_from_traceback
    def replay(
        func: Callable,
        state: tuple | ExcInfo,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if isinstance(state, ExcInfo):
            tb = state.tb.as_traceback().tb_next
            raise state.e.with_traceback(tb)

        data, headers, url, status = state
        return urllib.response.addinfourl(io.BytesIO(data), headers, url, status)

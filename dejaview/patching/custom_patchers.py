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
# Networking – urllib
# ---------------------------------------------------------------------------


class _ReplayHTTPResponse:
    """Lightweight stand-in for ``http.client.HTTPResponse`` during replay."""

    def __init__(
        self,
        data: bytes,
        status: int,
        reason: str,
        headers: list[tuple[str, str]],
        url: str,
    ) -> None:
        self._data = data
        self._stream = io.BytesIO(data)
        self.status = status
        self.code = status  # urllib compat
        self.reason = reason
        self.url = url
        self._headers = headers

    def read(self, amt: int | None = None) -> bytes:
        return self._stream.read(amt)  # type: ignore[arg-type]

    def readline(self) -> bytes:
        return self._stream.readline()

    def getheader(self, name: str, default: str | None = None) -> str | None:
        for k, v in self._headers:
            if k.lower() == name.lower():
                return v
        return default

    def getheaders(self) -> list[tuple[str, str]]:
        return list(self._headers)

    def info(self) -> _ReplayHTTPResponse:
        return self

    def geturl(self) -> str:
        return self.url

    def __enter__(self) -> _ReplayHTTPResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def close(self) -> None:
        pass


class UrlopenPatcher(Patcher[Any, tuple]):
    """Patcher for ``urllib.request.urlopen``.

    During play the URL is fetched, the full response body is read, and
    metadata (status, headers, URL) is stored.  On replay a lightweight
    :class:`_ReplayHTTPResponse` pre-loaded with the stored data is
    returned.
    """

    @staticmethod
    def play(func: Callable, *args: Any, **kwargs: Any):  # noqa: ANN205
        resp = func(*args, **kwargs)
        data: bytes = resp.read()
        status: int = getattr(resp, "status", getattr(resp, "code", 200))
        reason: str = getattr(resp, "reason", "OK")
        url: str = getattr(resp, "url", "")
        headers: list[tuple[str, str]] = (
            list(resp.getheaders()) if hasattr(resp, "getheaders") else []
        )
        resp.close()
        state = (data, status, reason, headers, url)

        def run() -> Any:
            return _ReplayHTTPResponse(data, status, reason, headers, url)

        return run, state

    @staticmethod
    def replay(func: Callable, state: tuple, *args: Any, **kwargs: Any) -> Any:
        data, status, reason, headers, url = state
        return _ReplayHTTPResponse(data, status, reason, headers, url)

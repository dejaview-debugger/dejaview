"""Custom patchers for socket operations.

These patchers handle the special requirements of socket patching:
- ``socket.socket.__init__`` needs to preserve object identity while
  replaying deterministic slot field values (family, type, proto).
- AF_UNIX sockets must be skipped to avoid breaking multiprocessing
  internals (see !33).
"""

from __future__ import annotations

import socket
import sys
from typing import Any, Callable

import tblib  # type: ignore[import-untyped]
import tblib.pickling_support  # type: ignore[import-untyped]

from dejaview.patching.patcher import ExcInfo, Patcher
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


class SocketInitPatcher(Patcher[Any, tuple]):
    """Patcher for ``socket.socket.__init__``.

    On play, the real ``__init__`` runs and slot fields are stored.
    On replay, the real ``__init__`` is called (to create a valid socket
    object) and then the stored slot field values are restored so that
    the socket has deterministic identity.

    AF_UNIX sockets are never patched — they pass through directly.
    """

    @staticmethod
    def play(func: Callable, *args: Any, **kwargs: Any):  # noqa: ANN205
        if _is_af_unix_from_init_args(*args, **kwargs):
            func(*args, **kwargs)
            return (lambda: None), None

        exc_info: ExcInfo | None = None
        try:
            func(*args, **kwargs)
        except BaseException as err:
            tblib.pickling_support.install(err)
            _, ev, tb = sys.exc_info()
            assert ev is not None
            exc_info = ExcInfo(e=ev, tb=tblib.Traceback(tb))

        if exc_info is not None:
            captured = exc_info

            @hide_from_traceback
            def run_err() -> None:
                tb = captured.tb.as_traceback().tb_next
                raise captured.e.with_traceback(tb)

            return run_err, captured

        # args[0] is self
        self = args[0]
        state = (self.family, self.type, self.proto)
        return (lambda: None), state

    @staticmethod
    @hide_from_traceback
    def replay(
        func: Callable, state: tuple | ExcInfo | None, *args: Any, **kwargs: Any
    ) -> Any:
        if state is None:
            # AF_UNIX — pass through
            return func(*args, **kwargs)

        if isinstance(state, ExcInfo):
            tb = state.tb.as_traceback().tb_next
            raise state.e.with_traceback(tb)

        # Create a real socket so internal C-level state is valid
        func(*args, **kwargs)
        self = args[0]
        family, sock_type, proto = state
        # Overwrite the slot fields to match the original
        self.family = family
        self.type = sock_type
        self.proto = proto
        return None


class SocketMethodPatcher(Patcher[Any, tuple[Any | None, BaseException | None]]):
    """Patcher for socket instance methods (bind, recv, send, etc.).

    Skips patching for AF_UNIX sockets. Otherwise behaves like
    GenericPatcher.
    """

    @staticmethod
    def play(func: Callable, *args: Any, **kwargs: Any):  # noqa: ANN205
        # args[0] is self for instance methods
        if args and isinstance(args[0], socket.socket) and _is_af_unix(args[0]):
            passthrough = func(*args, **kwargs)
            return (lambda: passthrough), None

        exc_info: ExcInfo | None = None
        ret: Any | None = None
        try:
            ret = func(*args, **kwargs)
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

            return run_err, (None, exc_info)

        state = (ret, None)
        return (lambda: ret), state

    @staticmethod
    @hide_from_traceback
    def replay(func: Callable, state: tuple | None, *args: Any, **kwargs: Any) -> Any:
        if state is None:
            # AF_UNIX — pass through
            return func(*args, **kwargs)

        ret, exc_info = state
        if exc_info is not None:
            tb = exc_info.tb.as_traceback().tb_next
            raise exc_info.e.with_traceback(tb)
        return ret

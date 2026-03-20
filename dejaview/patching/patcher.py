"""Definitions for patcher abstractions used by the patching helpers."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Callable, Protocol

import tblib  # type: ignore[import-untyped]
import tblib.pickling_support  # type: ignore[import-untyped]

from dejaview.patching.util import hide_from_traceback


class Patcher[TReturn, TState](Protocol):
    """Protocol implemented by patchers.

    Patchers record the result of a call during "play" and can later "replay"
    that result without invoking the original function again.
    """

    @staticmethod
    def play(
        func: Callable[..., TReturn],
        *args: Any,
        **kwargs: Any,
    ) -> tuple[Callable[[], TReturn], TState]: ...

    @staticmethod
    def replay(
        func: Callable[..., TReturn],
        state: TState,
        *args: Any,
        **kwargs: Any,
    ) -> TReturn: ...


@dataclass
class ExcInfo:
    e: BaseException
    tb: tblib.Traceback


@dataclass
class GenericPatcherState:
    return_value: Any
    exc_info: ExcInfo | None


class GenericPatcher(Patcher[Any, GenericPatcherState]):
    """Default patcher that stores return value and any raised exception."""

    @staticmethod
    @hide_from_traceback
    def return_or_raise(state: GenericPatcherState) -> Any:
        info = state.exc_info
        if info is not None:
            # skip an extra frame to hide the `ret = func(*args, **kwargs)` line
            tb = info.tb.as_traceback().tb_next
            raise info.e.with_traceback(tb)
        return state.return_value

    @staticmethod
    def play(func, *args, **kwargs):
        # Lazy import to avoid circular dependency
        from dejaview.patching.patching import (  # noqa: PLC0415
            PatchingMode,
            set_patching_mode,
        )

        try:
            # Do not patch any functions called by func because they won't be called
            # again during replay. If we did patch them, replay will diverge due to the
            # mismatch in the number of calls.
            with set_patching_mode(PatchingMode.OFF):
                ret = func(*args, **kwargs)
            state = GenericPatcherState(return_value=ret, exc_info=None)
        except BaseException as err:
            tblib.pickling_support.install(err)
            _, ev, tb = sys.exc_info()
            assert ev is not None
            exc_info = ExcInfo(e=ev, tb=tblib.Traceback(tb))
            state = GenericPatcherState(return_value=None, exc_info=exc_info)

        @hide_from_traceback
        def run() -> Any:
            return GenericPatcher.return_or_raise(state)

        return run, state

    @staticmethod
    @hide_from_traceback
    def replay(func, state, *args, **kwargs):
        return GenericPatcher.return_or_raise(state)


# @patch(patcher=GenericPatcher)
# def my_func():
#     pass

# example use case: patch just open() function instead of all functions on the File

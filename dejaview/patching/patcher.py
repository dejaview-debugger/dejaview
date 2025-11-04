"""Definitions for patcher abstractions used by the patching helpers."""

from __future__ import annotations

from typing import Any, Callable, Protocol


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


class GenericPatcher(Patcher[Any, tuple[Any | None, BaseException | None]]):
    """Default patcher that stores return value and any raised exception."""

    @staticmethod
    def return_or_raise(state: tuple[Any | None, BaseException | None]) -> Any:
        ret, ex = state
        if ex is not None:
            raise ex
        return ret

    @staticmethod
    def play(func, *args, **kwargs):
        ret: Any | None = None
        ex: BaseException | None = None
        try:
            ret = func(*args, **kwargs)
        except Exception as err:  # noqa: BLE001 - re-raising below preserves context
            ex = err
        state = (ret, ex)

        def run() -> Any:
            return GenericPatcher.return_or_raise(state)

        return run, state

    @staticmethod
    def replay(func, state, *args, **kwargs):
        return GenericPatcher.return_or_raise(state)


# @patch(patcher=GenericPatcher)
# def my_func():
#     pass

# example use case: patch just open() function instead of all functions on the File

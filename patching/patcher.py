"""
examples of function patching
"""

from typing import Callable, Protocol, Tuple, TypeVar

TState = TypeVar("TState")
TReturn = TypeVar("TReturn")


class Patcher(Protocol[TReturn, TState]):
    @staticmethod
    def play(func, *args, **kwargs) -> Tuple[Callable[[], TReturn], TState]: ...

    @staticmethod
    def replay(func, state: TState, *args, **kwargs) -> Callable[[], TReturn]: ...


class GenericPatcher(Patcher[TReturn, TState]):
    @staticmethod
    def return_or_raise(state: TState) -> TReturn:
        ret, ex = state
        if ex is not None:
            raise ex
        return ret

    @staticmethod
    def play(func, *args, **kwargs):
        ret = None
        ex = None
        try:
            ret = func(*args, **kwargs)
        except Exception as e:
            ex = e
        state = (ret, ex)

        def run():
            return GenericPatcher.return_or_raise(state)

        return run, state

    @staticmethod
    def replay(func, state, *args, **kwargs):
        return GenericPatcher.return_or_raise(state)


# @patch(patcher=GenericPatcher)
# def my_func():
#     pass

# example use case: patch just open() function instead of all functions on the File

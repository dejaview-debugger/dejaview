"""
examples of function patching
"""

from typing import Protocol, Tuple, TypeVar

TState = TypeVar("TState")
TReturn = TypeVar("TReturn")


class Patcher(Protocol[TReturn, TState]):
    @staticmethod
    def play(func, *args, **kwargs) -> Tuple[TReturn, TState]: ...

    @staticmethod
    def replay(func, state: TState, *args, **kwargs) -> TReturn: ...


class GenericPatcher(Patcher[TReturn, TState]):
    def __init__(self, func):
        self.func = func

    @staticmethod
    def play(func, *args, **kwargs):
        ret = func(*args, **kwargs)
        return ret, ret

    @staticmethod
    def replay(func, state, *args, **kwargs):
        return state


# @patch(patcher=GenericPatcher)
# def my_func():
#     pass

# example use case: patch just open() function instead of all functions on the File

from collections.abc import Callable
from functools import wraps
from typing import Any


def hide_from_traceback(func: Callable[..., Any]):
    """Decorator to hide a function's frame from tracebacks raised from it."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any):
        try:
            return func(*args, **kwargs)
        except BaseException as e:
            # Go 2 frames up to skip our own frame and func's frame
            tb = e.__traceback__
            for _ in range(2):
                if tb is not None:
                    tb = tb.tb_next
            if tb is not None:
                e.__traceback__ = tb
            # If it was less than 2 frames deep, it means that the exception came
            # from func itself, so we shouldn't hide it.
            raise

    return wrapper

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
            assert e.__traceback__ is not None
            assert e.__traceback__.tb_next is not None
            e.__traceback__ = e.__traceback__.tb_next.tb_next
            raise

    return wrapper

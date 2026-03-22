import inspect
import sys
import types
from contextlib import contextmanager
from contextvars import ContextVar
from enum import Enum
from functools import wraps
from typing import Any, Callable, ContextManager, Sequence, cast
from unittest.mock import patch

from dejaview.patching.backdoor import is_replay
from dejaview.patching.patcher import GenericPatcher, Patcher
from dejaview.patching.state_store import StateStore
from dejaview.patching.util import hide_from_traceback

ResetFunc = Callable[[int], None]
CaptureFunc = Callable[[], int]

reset_funcs: list[ResetFunc] = []
capture_funcs: list[CaptureFunc] = []


def capture() -> list[int]:
    return [func() for func in capture_funcs]


def reset(snapshot: Sequence[int]) -> None:
    assert len(snapshot) == len(reset_funcs)
    for func, seq in zip(reset_funcs, snapshot):
        func(seq)


class PatchingMode(Enum):
    NORMAL = 0
    OFF = 1
    MUTED = 2


_patching_mode = ContextVar("patching_mode", default=PatchingMode.OFF)
DEBUG = False


def debug_log(*args: Any) -> None:
    if DEBUG:
        print(*args, file=sys.stderr, flush=True)


def get_patching_mode():
    return _patching_mode.get()


@contextmanager
def set_patching_mode(mode: PatchingMode):
    token = _patching_mode.set(mode)
    try:
        yield
    finally:
        _patching_mode.reset(token)


# Decorator to log function results
def log_results[F: Callable[..., Any]](
    func: F, patcher: type[Patcher[Any, Any]] = GenericPatcher
) -> F:
    # assert issubclass(patcher, Patcher)

    current_seq = -1  # first sequence number should be 0

    def reset_seq(seq: int) -> None:
        nonlocal current_seq
        current_seq = seq

    capture_funcs.append(lambda: current_seq)
    reset_funcs.append(reset_seq)

    @wraps(func)
    @hide_from_traceback
    def wrapper(*args: Any, **kwargs: Any):
        mode = get_patching_mode()
        if mode == PatchingMode.OFF:
            return func(*args, **kwargs)

        nonlocal current_seq
        current_seq += 1
        # print(
        #     "Current sequence number:",
        #     current_seq,
        #     "Function:",
        #     func.__name__,
        #     "contains:",
        #     StateStore.get(func).contains(current_seq),
        # )
        should_play = is_replay() != StateStore.get(func).contains(current_seq)

        if should_play:
            raise RuntimeError(
                f"Replay divergence in patched function {func.__qualname__}\n"
                f"is_replay={is_replay()}\n"
                f"current_seq={current_seq}\n"
                f"stored={len(StateStore.get(func).store)}"
            )
        if is_replay():
            state = StateStore.get(func).get_state(current_seq)
            return patcher.replay(func, state, *args, **kwargs)
        else:
            # play
            run, state = patcher.play(func, *args, **kwargs)
            StateStore.get(func).set_state(current_seq, state)
            return run()

    return cast(F, wrapper)


# Function to apply logging to all functions in a module
def patch_all_functions_in_module(module: types.ModuleType) -> None:
    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, types.FunctionType) and name != "log_results":
            setattr(module, name, log_results(obj))  # Decorate only real functions


# Decorates a function
def decorate_func(
    func: Callable[..., Any],
    decorator: Callable[[Callable[..., Any]], Callable[..., Any]],
) -> None:
    # Method 1
    module = inspect.getmodule(func)
    if module is not None:
        setattr(module, func.__name__, decorator(func))
        return
    # Method 2
    if hasattr(func, "__self__"):
        setattr(globals().get(func.__self__.__module__), "random", decorator(func))
        return
    # Method 3
    owner = getattr(func, "__objclass__", None)
    assert owner is not None
    setattr(owner, func.__name__, decorator(func))


# Patches a function
def patch_func(
    func: Callable[..., Any],
    patcher: type[Patcher[Any, Any]] = GenericPatcher,
) -> None:
    decorate_func(func, lambda f: log_results(f, patcher))


class Patches:
    def __init__(self) -> None:
        self.mocks: list[ContextManager[Any]] = []

    def add(
        self,
        mock: ContextManager[Any],
    ) -> None:
        mock.__enter__()
        self.mocks.append(mock)

    def replace(
        self,
        obj: Any,
        attribute: str,
        new_value: Any,
    ) -> None:
        mock = patch.object(obj, attribute, new_value)
        self.add(mock)

    def decorate(
        self,
        obj: Any,
        attribute: str,
        decorator: Callable[[Callable[..., Any]], Callable[..., Any]],
    ) -> None:
        original = getattr(obj, attribute)
        mock_bind = decorator(original)
        self.replace(obj, attribute, mock_bind)

    def patch(
        self,
        obj: Any,
        attribute: str,
        patcher: type[Patcher[Any, Any]] = GenericPatcher,
        should_patch: Callable[..., bool] | None = None,
    ) -> None:
        """
        Args:
            obj: Object containing the attribute to patch.
            attribute: Name of the attribute to patch.
            patcher: Patcher class to use for this patch.
            should_patch:
                Function that takes the same arguments as the patched function
                and returns a boolean indicating whether to apply the patch or not.
                If None, the patch is always applied.
                If provided, it must be deterministic for the same arguments.
        """
        if should_patch is None:
            self.decorate(obj, attribute, lambda f: log_results(f, patcher))
        else:

            def factory(func: Callable[..., Any]) -> Callable[..., Any]:
                patched = log_results(func, patcher)

                @wraps(func)
                @hide_from_traceback
                def wrapper(*args: Any, **kwargs: Any) -> Any:
                    if should_patch(*args, **kwargs):
                        return patched(*args, **kwargs)
                    else:
                        return func(*args, **kwargs)

                return wrapper

            self.decorate(obj, attribute, factory)

    def __enter__(self) -> "Patches":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        while self.mocks:
            self.mocks[-1].__exit__(exc_type, exc_val, exc_tb)
            self.mocks.pop()

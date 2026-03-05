import inspect
import math
import random
import types
from enum import Enum
from functools import wraps
from typing import Any, Callable, ContextManager, Sequence, cast
from unittest.mock import patch

from dejaview.patching.patcher import GenericPatcher, Patcher
from dejaview.patching.state_store import StateStore

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


class _PatchingState:
    mode: PatchingMode = PatchingMode.NORMAL


def get_patching_mode():
    return _PatchingState.mode


class SetPatchingMode:
    def __init__(self, mode: PatchingMode):
        self.mode = mode

    def __enter__(self):
        self.old = _PatchingState.mode
        _PatchingState.mode = self.mode

    def __exit__(self, exc_type, exc_val, exc_tb):
        _PatchingState.mode = self.old


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
    def wrapper(*args: Any, **kwargs: Any):
        if get_patching_mode() == PatchingMode.OFF:
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
        if not StateStore.get(func).contains(current_seq):
            # play
            run, state = patcher.play(func, *args, **kwargs)
            StateStore.get(func).set_state(current_seq, state)
            return run()
        else:
            # replay
            state = StateStore.get(func).get_state(current_seq)
            return patcher.replay(func, state, *args, **kwargs)

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
    ) -> None:
        self.decorate(obj, attribute, lambda f: log_results(f, patcher))

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


# Patching all functions in the current module
if __name__ == "__main__":
    log_results_fn = cast(Any, log_results)

    # Example functions to test
    @log_results_fn
    def add(a: int, b: int) -> int:
        return a + b

    @log_results_fn
    def multiply(a: int, b: int) -> int:
        return a * b

    patch_func(math.sin)
    patch_func(random.random)
    # patch_all_functions_in_module(sys.modules[__name__])

    snapshot = capture()

    # Call the functions to see the logging in action

    # Turn on logging mode
    log_results_fn.mode = True

    result_add = add(5, 3)
    result_multiply = multiply(2, 5)

    print(math.sin(0))

    # Turn on replay mode
    reset(snapshot)
    log_results_fn.mode = False

    assert add(0, 0) == 8
    assert multiply(0, 0) == 10
    assert math.sin(1) == 0.0

    # Turn on loggin mode
    snapshot = capture()
    log_results_fn.mode = True

    val1 = random.random()
    val2 = random.random()

    random.seed(0)  # Reset
    initial = random.random()

    reset(snapshot)
    log_results_fn.mode = False

    random.seed(0)  # Reset
    assert random.random() == val1
    assert random.random() == val2
    assert random.random() == initial

    log_results_fn.mode = True

    assert random.random() == initial

    # Test custom patcher

    class CustomPatcher(Patcher[int, int]):
        @staticmethod
        def play(func, *args, **kwargs):
            def run() -> int:
                return func(*args, **kwargs) + 1

            return run, 1

        @staticmethod
        def replay(func, state, *args, **kwargs):
            return state + 2

    patch_func(random.randint, CustomPatcher)

    snapshot = capture()
    log_results_fn.mode = True

    rand_val = random.randint(5, 5)
    assert rand_val == 6

    reset(snapshot)
    log_results_fn.mode = False

    assert random.randint(5, 5) == 3

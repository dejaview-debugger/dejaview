import inspect
import math
import random
import types
from functools import wraps
from typing import TypeVar, Type
from enum import Enum
import typing
from unittest.mock import patch

from .patcher import Patcher, GenericPatcher
from .state_store import StateStore


reset_funcs = []
capture_funcs = []


def capture():
    return [func() for func in capture_funcs]


def reset(snapshot):
    assert len(snapshot) == len(reset_funcs)
    for func, seq in zip(reset_funcs, snapshot):
        func(seq)


class PatchingMode(Enum):
    NORMAL = 0
    OFF = 1
    MUTED = 2


patching_mode = PatchingMode.NORMAL


def get_patching_mode():
    return patching_mode


class SetPatchingMode:
    def __init__(self, mode: PatchingMode):
        self.mode = mode

    def __enter__(self):
        global patching_mode
        self.old = patching_mode
        patching_mode = self.mode

    def __exit__(self, exc_type, exc_val, exc_tb):
        global patching_mode
        patching_mode = self.old


TPatcher = TypeVar("TPatcher", bound=Patcher)


# Decorator to log function results
def log_results(func, patcher: Type[TPatcher] = GenericPatcher):
    # assert issubclass(patcher, Patcher)

    current_seq = -1  # first sequence number should be 0

    def reset_seq(seq):
        nonlocal current_seq
        current_seq = seq

    capture_funcs.append(lambda: current_seq)
    reset_funcs.append(reset_seq)

    @wraps(func)
    def wrapper(*args, **kwargs):
        if get_patching_mode() == PatchingMode.OFF:
            return func(*args, **kwargs)

        nonlocal current_seq
        current_seq += 1
        # print("Current sequence number:", current_seq, "Function:", func.__name__, "contains:", StateStore.get(func).contains(current_seq))
        if not StateStore.get(func).contains(current_seq):
            # play
            run, state = patcher.play(func, *args, **kwargs)  # Execute the function
            StateStore.get(func).set_state(current_seq, state)
            return run()
        else:
            # replay
            state = StateStore.get(func).get_state(current_seq)
            return patcher.replay(func, state, *args, **kwargs)

    return wrapper


# Function to apply logging to all functions in a module
def patch_all_functions_in_module(module):
    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, types.FunctionType) and name != "log_results":
            setattr(module, name, log_results(obj))  # Decorate only real functions


# Decorates a function
def decorate_func(func, decorator: typing.Callable):
    # Method 1
    module = inspect.getmodule(func)
    if module is not None:
        setattr(inspect.getmodule(func), func.__name__, decorator(func))
        return
    # Method 2
    if hasattr(func, "__self__"):
        setattr(
            globals().get(func.__self__.__module__),
            "random",
            decorator(func),
        )
        return
    # Method 3
    setattr(func.__objclass__, func.__name__, decorator(func))


# Patches a function
def patch_func(func, patcher: Patcher = GenericPatcher):
    decorate_func(func, lambda f: log_results(f, patcher))


class Patches:
    def __init__(self):
        self.mocks = []

    def decorate(self, object, attribute, decorator: typing.Callable):
        original = getattr(object, attribute)
        mock_bind = decorator(original)
        mock = patch.object(object, attribute, mock_bind)
        mock.__enter__()
        self.mocks.append(mock)

    def patch(self, object, attribute, patcher: Patcher = GenericPatcher):
        self.decorate(object, attribute, lambda f: log_results(f, patcher))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        while self.mocks:
            self.mocks[-1].__exit__(exc_type, exc_val, exc_tb)
            self.mocks.pop()


# Patching all functions in the current module
if __name__ == "__main__":
    # Example functions to test
    @log_results
    def add(a, b):
        return a + b

    @log_results
    def multiply(a, b):
        return a * b

    patch_func(math.sin)
    patch_func(random.random)
    # patch_all_functions_in_module(sys.modules[__name__])

    snapshot = capture()

    # Call the functions to see the logging in action

    # Turn on logging mode
    log_results.mode = True

    result_add = add(5, 3)
    result_multiply = multiply(2, 5)

    print(math.sin(0))

    # Turn on replay mode
    reset(snapshot)
    log_results.mode = False

    assert add(0, 0) == 8
    assert multiply(0, 0) == 10
    assert math.sin(1) == 0.0

    # Turn on loggin mode
    snapshot = capture()
    log_results.mode = True

    val1 = random.random()
    val2 = random.random()

    random.seed(0)  # Reset
    initial = random.random()

    reset(snapshot)
    log_results.mode = False

    random.seed(0)  # Reset
    assert random.random() == val1
    assert random.random() == val2
    assert random.random() == initial

    log_results.mode = True

    assert random.random() == initial

    # Test custom patcher

    class CustomPatcher(Patcher[int, int]):
        @staticmethod
        def play(func, *args, **kwargs):
            return func(*args, **kwargs) + 1, 1

        @staticmethod
        def replay(func, state, *args, **kwargs):
            return state + 2

    patch_func(random.randint, CustomPatcher)

    snapshot = capture()
    log_results.mode = True

    rand_val = random.randint(5, 5)
    assert rand_val == 6

    reset(snapshot)
    log_results.mode = False

    assert random.randint(5, 5) == 3

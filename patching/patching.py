import inspect
import math
import random
import types
from functools import wraps
from typing import TypeVar, Type
from enum import Enum

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


skip_count = 0


def skip_patching():
    global skip_count
    skip_count += 1
    print("skip", skip_count)

def restore_patching():
    global skip_count
    skip_count -= 1
    assert skip_count >= 0
    print("restore", skip_count)

def is_skip_patching():
    return skip_count > 0


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
        if is_skip_patching():
            return func(*args, **kwargs)
        
        nonlocal current_seq
        current_seq += 1
        # print("Current sequence number:", current_seq, "Function:", func.__name__, "contains:", StateStore.get(func).contains(current_seq))
        if not StateStore.get(func).contains(current_seq):
            # play
            result, state = patcher.play(func, *args, **kwargs)  # Execute the function
            StateStore.get(func).set_state(current_seq, state)
            return result
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


# Patches a function
def patch_func(func, patcher=GenericPatcher):
    # Method 1
    module = inspect.getmodule(func)
    if module is not None:
        setattr(inspect.getmodule(func), func.__name__, log_results(func, patcher))
        return
    # Method 2
    try:
        setattr(
            globals().get(func.__self__.__module__),
            "random",
            log_results(func, patcher),
        )
    except:  # noqa: E722
        pass


# Example functions to test
@log_results
def add(a, b):
    return a + b


@log_results
def multiply(a, b):
    return a * b


# Patching all functions in the current module
if __name__ == "__main__":
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

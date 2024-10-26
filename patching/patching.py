import inspect
import math
import random
import types
from functools import wraps

from state_store import StateStore


# Decorator to log function results
def log_results(func):
    current_seq = -1  # first sequence number should be 0
    log_results.mode = True  # Play vs replay

    @wraps(func)
    def wrapper(*args, **kwargs):
        nonlocal current_seq
        current_seq += 1
        if log_results.mode: # TODO: switch to enum
            result = func(*args, **kwargs) # Execute the function
            StateStore.get(func).set_state(current_seq, result)
            return result
        return StateStore.get(func).get_state(current_seq)

    return wrapper


# Function to apply logging to all functions in a module
def patch_all_functions_in_module(module):
    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, types.FunctionType) and name != "log_results":
            setattr(module, name, log_results(obj))  # Decorate only real functions


# Patches a function
def patch_func(func):
    # Method 1
    module = inspect.getmodule(func)
    if module is not None:
        setattr(inspect.getmodule(func), func.__name__, log_results(func))
        return
    # Method 2
    try:
        setattr(globals().get(func.__self__.__module__), "random", log_results(func))
    except:
        pass


# Example functions to test
def add(a, b):
    return a + b


def multiply(a, b):
    return a * b


# Patching all functions in the current module
if __name__ == "__main__":
    import sys

    patch_func(math.sin)
    patch_func(random.random)
    patch_all_functions_in_module(sys.modules[__name__])

    # Call the functions to see the logging in action

    # Turn on logging mode
    log_results.mode = True

    result_add = add(5, 3)
    result_multiply = multiply(2, 5)

    print(math.sin(0))

    # Turn on replay mode
    log_results.mode = False

    print(add(0, 0), multiply(0, 0), math.sin(1))  # Should be 8 10 0

    # Turn on loggin mode
    log_results.mode = True

    val1 = random.random()
    val2 = random.random()

    random.seed(0)  # Reset
    initial = random.random()

    print(val1, val2, "initial", initial)

    log_results.mode = False

    random.seed(0)  # Reset
    print(random.random(), random.random())  # Identical line as before

    log_results.mode = True

    print(random.random())  # should be initial

import inspect
import logging
import types
import random
from functools import wraps

import math

from collections import defaultdict

# Decorator to log function results
def log_results(func):
    log_results.stored_res = defaultdict(list)
    log_results.current_seq = defaultdict(int) # func to int
    log_results.mode = True # Play vs replay
    @wraps(func)
    def wrapper(*args, **kwargs):
        if log_results.mode:
            result = func(*args, **kwargs)  # Execute the function
            log_results.stored_res[func].append(result)
            return result
        # Assume we magically have seq number
        seq_num = log_results.current_seq[func]
        log_results.current_seq[func] += 1
        return log_results.stored_res[func][seq_num]
    return wrapper

# Function to apply logging to all functions in a module
def patch_all_functions_in_module(module):
    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, types.FunctionType) and name != 'log_results':
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

    print(add(0, 0), multiply(0, 0), math.sin(1)) # Should be 8 10 0

    # Turn on loggin mode
    log_results.mode = True

    val1 = random.random()
    val2 = random.random()

    random.seed(0) # Reset
    initial = random.random()

    print(val1, val2, "initial", initial)

    log_results.mode = False

    random.seed(0) # Reset
    print(random.random(), random.random()) # Identical line as before

    log_results.mode = True

    print(random.random()) # should be initial

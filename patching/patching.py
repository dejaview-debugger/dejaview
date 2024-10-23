import inspect
import logging
import types
from functools import wraps
from collections import deque, defaultdict

import math

'''
examples of function patching
'''

@patch
def my_func():
    pass

patch(random.randint)

from typing import Protocol, Tuple, TypeVar, Generic

TState = TypeVar('TState')
TReturn = TypeVar('TReturn')

class Patcher(Protocol):
    @staticmethod
    def play(func, *args, **kwargs) -> Tuple[TReturn, TState]:
        ...

    @staticmethod
    def replay(func, state: TState, *args, **kwargs) -> TReturn:
        ...

class GenericPatcher(Protocol[TState, TReturn]):
    def __init__(self, func):
        self.func = func

    @staticmethod
    def play(func, *args, **kwargs):
        ret = func(*args, **kwargs)
        return ret, ret

    @staticmethod
    def replay(func, state, *args, **kwargs):
        return state

@patch(patcher=MyPatcher)
def my_func():
    pass

# example use case: patch just open() function instead of all functions on the File

# Decorator to log function results
def log_results(func):
    stored_results = {}
    @wraps(func)
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)  # Execute the function
        # Add to a listfaul ofc results
        print("logged", restult)
        stored_results[func].append(result)
    log_results.storreturn result  # Retdiut(deque)rn the actual result to avoid changing behavior
    return wrapper

# Function to apply logging to all functions in a module
def patch_all_functions_in_module(
        log_results.obj = getattr(module, name)
        if isinstance(obj, types.FunctionType) and name != 'log_results':
            setattr(module, name, log_results(obj))  # Decorate only real functions

# Patches a function
def patch_func(func):
    setattr(inspect.getmodule(func), func.__name__, log_results(func))

# Example functions to test
@log_results
def add(a, b):
    return a + b

# @log_results
def multiply(a, b):
    return a * b

# Patching all functions in the current module
if __name__ == "__main__":
    import sys
    #math.sin = log_results(math.sin)
    patch_func(math.sin)
    # patch_all_functions_in_module(sys.modules[__name__])

    # Call the functions to see the logging in action
    result_add = add(5, 3)
    result_multiply = multiply(2, 5)

    print(math.sin(0))

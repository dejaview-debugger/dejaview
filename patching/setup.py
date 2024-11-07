from functools import wraps
import random
import time

from .patching import decorate_func, patch_func, get_patching_mode, PatchingMode


# Pass through normally, but skip if muted
def mute_decorator(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if get_patching_mode() != PatchingMode.MUTED:
            return func(*args, **kwargs)

    return wrapper


def setup_patching():
    patch_func(time.time)
    patch_func(time.sleep)
    patch_func(random.SystemRandom.getrandbits)
    patch_func(random.random)
    patch_func(input)  # BUG we don't want to patch PDB's input()!!
    decorate_func(print, mute_decorator)

    # TODO: revert to original

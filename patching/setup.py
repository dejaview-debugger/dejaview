import random
import time

from .patching import patch_func


def setup_patching():
    patch_func(time.time)
    patch_func(time.sleep)
    patch_func(random.SystemRandom.getrandbits)
    patch_func(random.random)
    patch_func(input)  # BUG we don't want to patch PDB's input()!!

    # TODO: revert to original
    


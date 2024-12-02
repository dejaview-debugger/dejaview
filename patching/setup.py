from functools import wraps
import random
import time
import socket
import builtins

from .patching import (
    decorate_func,
    patch_mock,
    get_patching_mode,
    PatchingMode,
)


# Pass through normally, but skip if muted
def mute_decorator(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if get_patching_mode() != PatchingMode.MUTED:
            return func(*args, **kwargs)

    return wrapper


def setup_patching():
    patch_mock(time, "time")
    patch_mock(time, "sleep")
    patch_mock(random.SystemRandom, "getrandbits")
    patch_mock(random, "random")
    patch_mock(socket.socket, "bind")
    patch_mock(socket.socket, "recvfrom")
    patch_mock(socket.socket, "sendto")
    patch_mock(socket, "socket")
    patch_mock(builtins, "input")
    decorate_func(print, mute_decorator)  # mute print when stepping back

    # TODO: revert to original

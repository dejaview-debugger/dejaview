from functools import wraps
import random
import time
import socket
import builtins

from .patching import (
    decorate_func,
    Patches,
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
    p = Patches()
    p.patch(time, "time")
    p.patch(time, "sleep")
    p.patch(random.SystemRandom, "getrandbits")
    p.patch(random, "random")
    p.patch(socket.socket, "bind")
    p.patch(socket.socket, "recvfrom")
    p.patch(socket.socket, "sendto")
    p.patch(socket, "socket")
    p.patch(builtins, "input")
    p.decorate(builtins, "print", mute_decorator)  # mute print when stepping back
    return p

    # TODO: revert to original

import builtins
import getpass
import os
import random
import socket
import sys
import time
from functools import wraps

from dejaview.patching.patching import Patches, PatchingMode, get_patching_mode


# Pass through normally, but skip if muted
def mute_decorator(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if get_patching_mode() != PatchingMode.MUTED:
            return func(*args, **kwargs)

    return wrapper


def patch_datetime(p: Patches):
    # Patch the class methods to memoize their results
    import _pydatetime  # type: ignore[import-not-found]  # noqa: PLC0415

    sys.modules["datetime"] = _pydatetime
    import datetime  # noqa: PLC0415

    p.patch(datetime.datetime, "now")
    p.patch(datetime.datetime, "utcnow")
    p.patch(datetime.date, "today")


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
    p.patch(os, "getpid")
    p.patch(getpass, "getpass")
    p.patch(getpass, "getuser")
    p.decorate(builtins, "print", mute_decorator)  # mute print when stepping back

    # Patch datetime
    patch_datetime(p)

    return p

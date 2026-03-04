import builtins
import os
import random
import socket
import sys
import tempfile
import time
from contextlib import contextmanager
from functools import wraps

from dejaview import _memory_patch
from dejaview.patching.custom_patchers import TempDirPatcher, TempFilePatcher
from dejaview.patching.patching import Patches, PatchingMode, get_patching_mode


# Pass through normally, but skip if muted
def mute_decorator(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if get_patching_mode() != PatchingMode.MUTED:
            return func(*args, **kwargs)

    return wrapper


# Deterministic object IDs via Rust extension patch
@contextmanager
def memory_patch():
    _memory_patch.enable()
    try:
        yield
    finally:
        _memory_patch.disable()


def patch_datetime(p: Patches):
    # Patch the class methods to memoize their results
    import _pydatetime  # type: ignore[import-not-found]  # noqa: PLC0415

    sys.modules["datetime"] = _pydatetime
    import datetime  # noqa: PLC0415

    p.patch(datetime.datetime, "now")
    p.patch(datetime.datetime, "utcnow")
    p.patch(datetime.date, "today")


def patch_tempfile(p: Patches):
    p.patch(tempfile, "NamedTemporaryFile", TempFilePatcher)
    p.patch(tempfile, "TemporaryFile", TempFilePatcher)
    p.patch(tempfile, "TemporaryDirectory", TempDirPatcher)
    p.patch(tempfile, "mkdtemp")
    p.patch(tempfile, "mkstemp")
    p.patch(tempfile, "gettempdir")


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
    p.decorate(builtins, "print", mute_decorator)  # mute print when stepping back
    patch_datetime(p)
    p.add(memory_patch())

    patch_tempfile(p)

    return p

import builtins
import getpass
import os
import random
import socket
import sys
import time
import urllib.request
from contextlib import contextmanager
from functools import wraps

from dejaview import _memory_patch
from dejaview.patching.custom_patchers import UrlopenPatcher
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


@contextmanager
def datetime_patch():
    import _pydatetime  # type: ignore[import-not-found]  # noqa: PLC0415
    import datetime as old_datetime  # noqa: PLC0415

    # Force it to use the Python implementation of datetime over the native one.
    # Note that function patches are not needed because _pydatetime uses time module
    # as the only source of non-determinism.
    sys.modules["datetime"] = _pydatetime
    try:
        yield
    finally:
        sys.modules["datetime"] = old_datetime


def patch_urllib(p: Patches):
    # urlopen needs a custom patcher because HTTPS bypasses socket patches
    # (SSL read/write go through C-level _sslobj, not our patched socket methods).
    # Plain HTTP would work with socket patches alone, but HTTPS would not.
    p.patch(urllib.request, "urlopen", UrlopenPatcher)
    p.patch(urllib.request, "urlretrieve")


def setup_patching():
    p = Patches()

    p.patch(time, "sleep")
    p.patch(time, "time")
    p.patch(time, "time_ns")
    p.patch(time, "monotonic")
    p.patch(time, "monotonic_ns")
    p.patch(time, "perf_counter")
    p.patch(time, "perf_counter_ns")
    p.patch(time, "process_time")
    p.patch(time, "process_time_ns")
    p.patch(time, "thread_time")
    p.patch(time, "thread_time_ns")
    p.patch(time, "clock_gettime")
    p.patch(time, "clock_gettime_ns")
    p.patch(time, "clock_settime")
    p.patch(time, "clock_settime_ns")
    for func in ["ctime", "gmtime", "localtime"]:
        p.patch(time, func, should_patch=lambda seconds=None: seconds is None)
    # Note that functions like tzset depend only on environment variables, which
    # should replay deterministically with all patches in place.

    p.patch(random.SystemRandom, "getrandbits")
    p.patch(random, "random")

    # AF_UNIX sockets are used for inter-process communication so patching them breaks
    # multiprocessing (which we use for communicating to replay forks).
    def skip_system_socket(self: socket.socket, *args, **kwargs):
        return self.family != socket.AF_UNIX

    # TODO: Merge !24 which properly patches socket.
    p.patch(socket.socket, "bind", should_patch=skip_system_socket)
    p.patch(socket.socket, "recvfrom", should_patch=skip_system_socket)
    p.patch(socket.socket, "sendto", should_patch=skip_system_socket)
    # Note: socket.socket constructor patch removed because it breaks class identity.

    p.patch(builtins, "input")
    p.patch(os, "getpid")
    p.patch(getpass, "getpass")
    p.decorate(builtins, "print", mute_decorator)  # mute print when stepping back
    p.add(datetime_patch())
    p.add(memory_patch())

    patch_urllib(p)

    return p

import builtins
import os
import random
import socket
import sys
import time
from contextlib import contextmanager
from functools import wraps

from dejaview import _memory_patch
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


def patch_socket(p: Patches):
    p.patch(socket.socket, "bind")
    p.patch(socket.socket, "connect")
    p.patch(socket.socket, "listen")
    p.patch(socket.socket, "accept")
    p.patch(socket.socket, "send")
    p.patch(socket.socket, "sendto")
    p.patch(socket.socket, "sendall")
    p.patch(socket.socket, "recv")
    p.patch(socket.socket, "recvfrom")
    p.patch(socket.socket, "close")
    p.patch(socket.socket, "shutdown")
    p.patch(socket.socket, "setsockopt")
    p.patch(socket.socket, "getsockname")
    p.patch(socket, "socket")
    p.patch(socket, "getaddrinfo")
    p.patch(socket, "gethostname")
    p.patch(socket, "gethostbyname")
    p.patch(socket, "create_connection")


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
    p.patch(builtins, "input")
    p.patch(os, "getpid")
    p.decorate(builtins, "print", mute_decorator)  # mute print when stepping back
    p.add(datetime_patch())
    p.add(memory_patch())
    patch_socket(p)

    return p

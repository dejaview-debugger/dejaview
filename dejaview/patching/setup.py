import _pyio  # type: ignore[import-not-found]
import builtins
import getpass
import io
import os
import random
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from functools import wraps

from dejaview import _memory_patch
from dejaview.patching.custom_patchers import (
    PopenPatcher,
    SocketInitPatcher,
    UrlopenPatcher,
    _is_not_af_unix,
)
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
    # Patch __init__ instead of the module-level constructor to preserve
    # object identity. SocketInitPatcher replays deterministic slot fields
    # (family, type, proto) while keeping a real underlying socket object.
    p.patch(socket.socket, "__init__", SocketInitPatcher)

    # Instance methods skip AF_UNIX sockets to avoid breaking
    # multiprocessing internals (see !33).
    for method in (
        "bind",
        "connect",
        "listen",
        "accept",
        "send",
        "sendto",
        "sendall",
        "recv",
        "recvfrom",
        "close",
        "shutdown",
        "setsockopt",
        "getsockname",
    ):
        p.patch(socket.socket, method, should_patch=_is_not_af_unix)

    # Module-level functions are safe to use GenericPatcher
    p.patch(socket, "getaddrinfo")
    p.patch(socket, "gethostname")
    p.patch(socket, "gethostbyname")
    p.patch(socket, "create_connection")


def patch_subprocess(p: Patches):
    p.patch(subprocess, "run")
    p.patch(subprocess, "Popen", PopenPatcher)
    p.patch(subprocess, "check_output")
    p.patch(subprocess, "check_call")
    p.patch(subprocess, "call")
    p.patch(subprocess, "getoutput")
    p.patch(subprocess, "getstatusoutput")


def patch_io(p: Patches):
    # Replace concrete classes inside the already-imported io module with their
    # pure Python (_pyio) equivalents. The C _io classes call C-level syscalls
    # directly, bypassing os module patching. _pyio classes route through
    # os.open/os.read/os.write/etc., which are already patched.
    #
    # We patch names inside io rather than swapping sys.modules["io"], because
    # io defines ABC classes (IOBase, TextIOBase, etc.) that C _io objects are
    # registered into. Swapping the module would break isinstance checks for
    # pre-existing C io objects like sys.stdout.
    #
    # Caveat: code that did `from io import open` or `from io import FileIO`
    # before patching holds a direct reference to the C version, which this
    # patch cannot reach. No stdlib module does this (checked in CPython 3.12).
    # Third-party libraries are fine unless imported by dejaview before patching.
    for name in [
        "open",
        "FileIO",
        "BytesIO",
        "StringIO",
        "BufferedReader",
        "BufferedWriter",
        "BufferedRWPair",
        "BufferedRandom",
        "TextIOWrapper",
        "IncrementalNewlineDecoder",
        "text_encoding",
        "DEFAULT_BUFFER_SIZE",
    ]:
        p.replace(io, name, getattr(_pyio, name))
    p.replace(builtins, "open", _pyio.open)


def patch_urllib(p: Patches):
    # urlopen needs a custom patcher because HTTPS bypasses socket patches
    # (SSL read/write go through C-level _sslobj, not our patched socket methods).
    # Plain HTTP would work with socket patches alone, but HTTPS would not.
    p.patch(urllib.request, "urlopen", UrlopenPatcher)
    p.patch(urllib.request, "urlretrieve")


def patch_sys(p: Patches):
    # sys.getrefcount and sys.getsizeof return values that depend on CPython
    # internal state (reference counts, allocator layout) which can vary across
    # replays.
    p.patch(sys, "getrefcount")
    p.patch(sys, "getsizeof")

    # sys.stdin/stdout/stderr are C _io.TextIOWrapper objects created at
    # interpreter startup. They bypass os module patching (C-level syscalls).
    # stdin reads are non-deterministic (user input); stdout/stderr writes
    # would produce duplicate output on replay.
    p.patch(sys.stdin, "read")
    p.patch(sys.stdin, "readline")
    p.patch(sys.stdin, "readlines")
    # C TextIOWrapper.__next__ calls C-level readline directly, not
    # self.readline(), so iterating stdin needs a separate patch.
    p.patch(sys.stdin, "__next__")
    p.decorate(sys.stdout, "write", mute_decorator)
    p.decorate(sys.stderr, "write", mute_decorator)
    # Note: writelines routes through self.write(), so the mute covers it.


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
    p.patch(getpass, "getpass")
    p.decorate(builtins, "print", mute_decorator)  # mute print when stepping back
    p.add(datetime_patch())
    p.add(memory_patch())
    patch_socket(p)
    patch_subprocess(p)
    patch_urllib(p)
    patch_sys(p)
    patch_io(p)

    # Note: shutil doesn't need patching because its sources of non-determinism
    # (e.g. os functions) are already patched.

    return p

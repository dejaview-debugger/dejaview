import _pyio  # type: ignore[import-not-found]
import builtins
import getpass
import io
import linecache
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
from dejaview.patching.patcher import ScanDirPatcher
from dejaview.patching.patching import (
    Patches,
    PatchingMode,
    get_patching_mode,
    set_patching_mode,
)


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


def patch_os(p: Patches):
    # Patch os module non-deterministic functions

    # --- Process / user identity ---
    p.patch(os, "getpid")  # Process ID
    p.patch(os, "getppid")  # Parent process ID
    p.patch(os, "getuid")  # User ID
    p.patch(os, "getgid")  # Group ID
    p.patch(os, "geteuid")  # Effective user ID
    p.patch(os, "getegid")  # Effective group ID
    p.patch(os, "getlogin")  # Login name
    p.patch(os, "getpgid")  # Process group ID for a given pid
    p.patch(os, "getpgrp")  # Current process group ID
    p.patch(os, "getpriority")  # Process scheduling priority
    p.patch(os, "getresgid")  # Real, effective, saved group IDs
    p.patch(os, "getresuid")  # Real, effective, saved user IDs
    p.patch(os, "getsid")  # Session ID
    p.patch(os, "getgroups")  # Supplemental group IDs
    p.patch(os, "getgrouplist")  # Group list for a user

    # --- Environment ---
    # os.getenv and os.getenvb are inherently deterministic within the
    # scope of a single process. Since the environment is inherited as a
    # static snapshot at startup and subsequent forking utilizes Copy-on-Write
    # (COW) semantics, the environment remains isolated from external process
    # changes. Therefore, these operations do not require manual patching to
    # maintain determinism.
    # p.patch(os, "getenv")  # Environment variables (str)
    # p.patch(os, "getenvb")  # Environment variables (bytes)

    # --- System information ---
    p.patch(os, "times")  # CPU times
    p.patch(os, "uname")  # System information
    p.patch(os, "cpu_count")  # Number of CPUs
    p.patch(os, "getloadavg")  # System load averages
    p.patch(os, "confstr")  # System configuration string
    p.patch(os, "sysconf")  # System configuration value

    # --- Filesystem queries ---
    p.patch(os, "listdir")  # Directory listing (order varies)
    p.patch(os, "stat")  # File statistics
    p.patch(os, "lstat")  # Symlink statistics
    p.patch(os, "fstat")  # File statistics by fd
    p.patch(os, "statvfs")  # Filesystem statistics
    p.patch(os, "fstatvfs")  # Filesystem statistics by fd
    p.patch(os, "readlink")  # Read symlink target
    p.patch(os, "access")  # Check file access permissions
    p.patch(os, "getxattr")  # Get extended file attribute
    p.patch(os, "listxattr")  # List extended file attributes
    p.patch(os, "fpathconf")  # File configuration by fd
    p.patch(os, "pathconf")  # File configuration by path

    # --- Working directory ---
    p.patch(os, "getcwd")  # Current working directory (str)
    p.patch(os, "getcwdb")  # Current working directory (bytes)

    # --- Terminal / device ---
    p.patch(os, "get_terminal_size")  # Terminal window size
    p.patch(os, "isatty")  # Is fd a terminal
    p.patch(os, "ttyname")  # Terminal device name
    p.patch(os, "ctermid")  # Controlling terminal name
    p.patch(os, "device_encoding")  # Device encoding
    p.patch(os, "tcgetpgrp")  # Terminal foreground process group

    # --- File-descriptor state ---
    p.patch(os, "get_blocking")  # Blocking mode of fd
    p.patch(os, "get_inheritable")  # Inheritable flag of fd

    # --- Other queries ---
    p.patch(os, "get_exec_path")  # Execution search path
    p.patch(os, "urandom")  # Random bytes

    # --- Scheduling queries ---
    p.patch(os, "sched_getaffinity")  # CPU affinity set
    p.patch(os, "sched_getparam")  # Scheduling parameters
    p.patch(os, "sched_getscheduler")  # Scheduling policy
    p.patch(os, "sched_get_priority_max")  # Max scheduling priority
    p.patch(os, "sched_get_priority_min")  # Min scheduling priority
    p.patch(os, "sched_rr_get_interval")  # Round-robin time quantum

    # ================================================================
    # Side-effect functions: execute normally on the first call;
    # on replay the cached return value (typically None) is returned
    # and the real function is NOT called again.
    # ================================================================

    # --- File permissions / ownership ---
    p.patch(os, "chmod")
    p.patch(os, "fchmod")
    p.patch(os, "chown")
    p.patch(os, "fchown")
    p.patch(os, "lchown")

    # --- Directory changes ---
    p.patch(os, "chdir")
    p.patch(os, "fchdir")
    p.patch(os, "chroot")

    # --- Create / remove ---
    p.patch(os, "mkdir")
    p.patch(os, "rmdir")
    p.patch(os, "remove")
    p.patch(os, "unlink")
    # TODO: Gemini mentions some issues with double counting when
    # os.makedirs and/or os.removedirs are patched since it double
    # counts some sequence numbers. Explore this further.
    # NOTE: os.makedirs and os.removedirs are Python wrappers that
    # internally call the patched os.mkdir / os.rmdir. They get
    # determinism automatically from the patched C-level functions
    # they delegate to.

    # --- Rename / move ---
    p.patch(os, "rename")
    p.patch(os, "replace")
    # NOTE: os.renames is a Python wrapper that calls os.rename,
    # os.makedirs, and os.removedirs inherits determinism from
    # patched os.rename.

    # --- Links ---
    p.patch(os, "link")
    p.patch(os, "symlink")

    # --- Truncation ---
    p.patch(os, "truncate")
    p.patch(os, "ftruncate")

    # --- Timestamps ---
    p.patch(os, "utime")

    # --- Extended attributes ---
    p.patch(os, "setxattr")
    p.patch(os, "removexattr")

    # --- Environment mutation ---
    # `os.putenv` and `os.unsetenv` do not need to be patched since
    # during replay, Dejaview will go back to a previous snapshot with
    # the previous environmental variables and the unpatched `putenv` 
    # and `unsetenv` should be rerun to ensure no divergence during replays.

    # --- Process identity setters ---
    p.patch(os, "setuid")
    p.patch(os, "setgid")
    p.patch(os, "seteuid")
    p.patch(os, "setegid")
    p.patch(os, "setreuid")
    p.patch(os, "setregid")
    p.patch(os, "setresuid")
    p.patch(os, "setresgid")
    p.patch(os, "setpgid")
    p.patch(os, "setpgrp")
    p.patch(os, "setsid")
    p.patch(os, "setgroups")
    p.patch(os, "initgroups")
    p.patch(os, "setpriority")

    # --- Side-effect with return value ---
    p.patch(os, "nice")  # Returns new niceness
    p.patch(os, "umask")  # Returns previous mask

    # --- Sync / flush ---
    p.patch(os, "fdatasync")
    p.patch(os, "fsync")
    p.patch(os, "sync")

    # --- FD state setters ---
    p.patch(os, "set_blocking")
    p.patch(os, "set_inheritable")

    # --- Close ---
    p.patch(os, "close")
    p.patch(os, "closerange")

    # --- File locking ---
    p.patch(os, "lockf")

    # --- Terminal setters ---
    p.patch(os, "tcsetpgrp")
    p.patch(os, "login_tty")

    # --- Namespace ---
    p.patch(os, "setns")
    p.patch(os, "unshare")

    # --- File advice / allocation ---
    p.patch(os, "posix_fadvise")
    p.patch(os, "posix_fallocate")

    # --- Scheduling setters ---
    p.patch(os, "sched_setaffinity")
    p.patch(os, "sched_setparam")
    p.patch(os, "sched_setscheduler")
    p.patch(os, "sched_yield")

    # --- Subprocess (synchronous) ---
    p.patch(os, "system")  # Returns exit code

    # --- Process signaling / waiting ---
    # These functions are used by snapshot internals and can still be
    # patched safely because snapshot code executes those internal calls
    # inside set_patching_mode(PatchingMode.OFF).
    p.patch(os, "kill")
    p.patch(os, "killpg")
    p.patch(os, "wait")
    p.patch(os, "wait3")
    p.patch(os, "wait4")
    p.patch(os, "waitid")
    p.patch(os, "waitpid")

    # --- Spawn family ---
    # All os.spawn* and os.posix_spawn* functions return a plain integer
    # child PID, which GenericPatcher can pickle and replay without issue.
    # During play the child is actually spawned; during replay the memoized
    # PID is returned without touching the kernel.  Because os.wait* is also
    # patched, any subsequent wait on that PID is equally non-blocking on
    # replay — the memoized wait result is returned immediately.
    p.patch(os, "spawnl")
    p.patch(os, "spawnle")
    p.patch(os, "spawnlp")
    p.patch(os, "spawnlpe")
    p.patch(os, "spawnv")
    p.patch(os, "spawnve")
    p.patch(os, "spawnvp")
    p.patch(os, "spawnvpe")
    p.patch(os, "posix_spawn")
    p.patch(os, "posix_spawnp")
    p.patch(os, "fork")
    #
    # SKIPPED – os.forkpty
    #   Produces a PTY-backed child process with terminal side-effects.
    #   Left unpatched for now; os.fork is patched and safe_fork uses
    #   set_patching_mode(PatchingMode.OFF) for debugger-internal forks.
    #
    # SKIPPED – os.abort / os._exit
    #   Process-termination primitives.  During play they kill the
    #   process before any state can be recorded.
    #
    # SKIPPED – os.exec* family (execl, execle, execv, execve, …)
    #   These replace the current process image.  The process is gone
    #   after the call so there is nothing to replay.
    #
    # SKIPPED – os.popen / os.fdopen
    #   Return file-like objects (io.BufferedRandom, subprocess.Popen
    #   wrappers, etc.) that are not picklable, so GenericPatcher cannot
    #   serialise the return value for replay.

    # ================================================================
    # Iterator-returning functions
    # ================================================================
    # Iterator patchers record values lazily as user code consumes them.
    # Replay only reproduces the already-consumed prefix.

    # os.scandir returns a context-manager iterator that cannot be
    # re-iterated once exhausted. ScanDirPatcher records consumed
    # entry names and rebuilds real os.DirEntry objects on replay.
    p.patch(os, "scandir", ScanDirPatcher)

    # SKIPPED – os.walk / os.fwalk
    #   These are Python generators that internally call the patched
    #   os.scandir (and os.open, os.close for fwalk).  Because the
    #   low-level functions they delegate to are already patched, walk
    #   and fwalk automatically produce deterministic results on
    #   replay without needing their own patch.  Patching them would
    #   cause double-counting of sequence numbers (the wrapper AND
    #   its inner scandir/open/close calls both advance).

    # ================================================================
    # Low-level I/O: file-descriptor operations.  Cached return
    # values ensure consistency during replay even though the
    # underlying fds may not correspond to real kernel objects.
    # ================================================================

    p.patch(os, "open")  # Returns fd (int)
    p.patch(os, "read")
    p.patch(os, "readv")
    p.patch(os, "pread")
    p.patch(os, "preadv")
    p.patch(os, "write")
    p.patch(os, "writev")
    p.patch(os, "pwrite")
    p.patch(os, "pwritev")
    p.patch(os, "lseek")
    p.patch(os, "dup")
    p.patch(os, "dup2")
    p.patch(os, "pipe")
    p.patch(os, "pipe2")
    p.patch(os, "sendfile")
    p.patch(os, "splice")
    p.patch(os, "openpty")
    p.patch(os, "mkfifo")
    p.patch(os, "mknod")
    p.patch(os, "eventfd")
    p.patch(os, "eventfd_read")
    p.patch(os, "eventfd_write")

    # Prevent linecache (used by pdb) from calling the patched
    # os.stat/os.lstat during source lookups.  Those internal
    # calls would advance the sequence counter and corrupt the
    # replay state.
    _orig_checkcache = linecache.checkcache

    @wraps(_orig_checkcache)
    def _safe_checkcache(filename=None):
        with set_patching_mode(PatchingMode.OFF):
            return _orig_checkcache(filename)

    p.replace(linecache, "checkcache", _safe_checkcache)


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

    # Patching Random
    # random still need to be patched despite `os.urandom` being patched
    # else `dejaview/tests/test_reverse.py::test_random_idempotence` fails
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

    patch_os(p)
    return p

import ctypes
import os
import random
import signal
import sys

from dejaview.patching.patching import PatchingMode, set_patching_mode


# source: https://gist.github.com/qxcv/fe5be4d14f855fedf7a5db723aad22c2
def exit_with_parent():
    """
    Ensure that this process receives SIGTERM when its parent dies.
    """
    assert sys.platform == "linux", "exit_with_parent is only supported on Linux"
    libc = ctypes.CDLL("libc.so.6")
    PR_SET_PDEATHSIG = 1
    retcode = libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    if retcode != 0:
        raise RuntimeError("prctl failed with code {}".format(retcode))

    # Use libc directly instead of os.getppid()/os.getpid() to
    # avoid going through the patching wrapper, which would
    # corrupt the sequence counter before StateStore is
    # deserialized in replay processes.
    with set_patching_mode(PatchingMode.OFF):
        if os.getppid() == 1:
            os.kill(os.getpid(), signal.SIGTERM)


@set_patching_mode(PatchingMode.OFF)
def safe_fork() -> int:
    """
    Fork a new process that will exit when the parent process exits.
    Also preserves randomness state across the forks.
    """
    # We have to save and restore the random state
    # because by default they are changed during fork
    random_state = random.getstate()
    with set_patching_mode(PatchingMode.OFF):
        pid = os.fork()
    random.setstate(random_state)
    if pid == 0:
        # in child process
        exit_with_parent()
    return pid

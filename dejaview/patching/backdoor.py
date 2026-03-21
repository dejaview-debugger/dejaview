_is_replay = False


def is_replay() -> bool:
    """
    Backdoor to check whether we're currently replaying.
    This is used by patching and also by tests to enforce divergence.
    """
    return _is_replay

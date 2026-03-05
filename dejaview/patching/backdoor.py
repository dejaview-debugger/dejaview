_is_replay = False


def is_replay() -> bool:
    """
    Backdoor to check whether we're currently replaying.
    This is used to force non-determinism in DejaView's own tests.

    This is only effective when DejaView runs in testing mode.
    """
    return _is_replay

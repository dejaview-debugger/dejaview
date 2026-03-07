"""Definitions for patcher abstractions used by the patching helpers."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Protocol

import tblib  # type: ignore[import-untyped]
import tblib.pickling_support  # type: ignore[import-untyped]

from dejaview.patching.util import hide_from_traceback


class Patcher[TReturn, TState](Protocol):
    """Protocol implemented by patchers.

    Patchers record the result of a call during "play" and can later "replay"
    that result without invoking the original function again.
    """

    @staticmethod
    def play(
        func: Callable[..., TReturn],
        *args: Any,
        **kwargs: Any,
    ) -> tuple[Callable[[], TReturn], TState]: ...

    @staticmethod
    def replay(
        func: Callable[..., TReturn],
        state: TState,
        *args: Any,
        **kwargs: Any,
    ) -> TReturn: ...


@dataclass
class ExcInfo:
    e: BaseException
    tb: tblib.Traceback


@dataclass
class GenericPatcherState:
    return_value: Any
    exc_info: ExcInfo | None


class GenericPatcher(Patcher[Any, GenericPatcherState]):
    """Default patcher that stores return value and any raised exception."""

    @staticmethod
    @hide_from_traceback
    def return_or_raise(state: GenericPatcherState) -> Any:
        info = state.exc_info
        if info is not None:
            # skip an extra frame to hide the `ret = func(*args, **kwargs)` line
            tb = info.tb.as_traceback().tb_next
            raise info.e.with_traceback(tb)
        return state.return_value

    @staticmethod
    def play(func, *args, **kwargs):
        # Lazy import to avoid circular dependency
        from dejaview.patching.patching import (  # noqa: PLC0415
            PatchingMode,
            set_patching_mode,
        )

        try:
            # Do not patch any functions called by func because they won't be called
            # again during replay. If we did patch them, replay will diverge due to the
            # mismatch in the number of calls.
            with set_patching_mode(PatchingMode.OFF):
                ret = func(*args, **kwargs)
            state = GenericPatcherState(return_value=ret, exc_info=None)
        except BaseException as err:
            tblib.pickling_support.install(err)
            _, ev, tb = sys.exc_info()
            assert ev is not None
            exc_info = ExcInfo(e=ev, tb=tblib.Traceback(tb))
            state = GenericPatcherState(return_value=None, exc_info=exc_info)

        @hide_from_traceback
        def run() -> Any:
            return GenericPatcher.return_or_raise(state)

        return run, state

    @staticmethod
    @hide_from_traceback
    def replay(func, state, *args, **kwargs):
        return GenericPatcher.return_or_raise(state)


class _ReplayableIterator:
    """Iterator wrapper that also acts as a context manager.

    Used by ``IteratorPatcher`` so that patched generators (``os.walk``,
    ``os.fwalk``) and context-manager iterators (``os.scandir``) can be
    replayed from a stored list without re-executing the original function.

    The class delegates to a C-level ``list_iterator`` for both
    ``__iter__`` and ``__next__``, which prevents ``pdb``'s trace hook
    from stopping on every ``__next__`` call inside list comprehensions
    (CPython 3.12 PEP 709) and avoids extra frame events that would
    confuse the step-back snapshot mechanism.
    """

    def __init__(self, items: list[Any]) -> None:
        self._iter: Iterator[Any] = iter(items)

    def __iter__(self) -> "_ReplayableIterator":
        return self

    def __next__(self) -> Any:
        return next(self._iter)

    def __enter__(self) -> "_ReplayableIterator":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def close(self) -> None:
        pass


class IteratorPatcher(Patcher[Any, tuple[list[Any] | None, BaseException | None]]):
    """Patcher for iterator/generator-returning functions.

    During *play*, the iterator is eagerly consumed into a list and the list
    is stored as the replay state.  During *replay*, a fresh
    ``_ReplayableIterator`` over the stored list is returned so callers can
    iterate again without re-executing the original function.

    .. note::

       The items in the list must be picklable because the snapshot
       mechanism sends ``StateStore`` data through ``multiprocessing``
       queues.  For ``os.scandir`` (whose ``DirEntry`` objects are
       **not** picklable), use ``ScanDirPatcher`` instead.
    """

    @staticmethod
    def play(
        func: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> tuple[Callable[[], Any], tuple[list[Any] | None, BaseException | None]]:
        ret: list[Any] | None = None
        ex: BaseException | None = None
        try:
            ret = list(func(*args, **kwargs))
        except Exception as err:  # noqa: BLE001
            ex = err
        state = (ret, ex)

        def run() -> Any:
            if ex is not None:
                raise ex
            return _ReplayableIterator(ret)  # type: ignore[arg-type]

        return run, state

    @staticmethod
    def replay(
        func: Callable[..., Any],
        state: tuple[list[Any] | None, BaseException | None],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        ret, ex = state
        if ex is not None:
            raise ex
        return _ReplayableIterator(ret)  # type: ignore[arg-type]


class _PicklableDirEntry:
    """Picklable stand-in for ``os.DirEntry``.

    Eagerly captures every property/method result of a live ``DirEntry``
    so the object can survive pickle round-trips (e.g. through
    ``multiprocessing`` queues used by the snapshot mechanism) while
    still providing the same interface that callers like ``os.walk``
    expect.
    """

    __slots__ = (
        "name",
        "path",
        "_inode",
        "_is_dir",
        "_is_dir_nf",
        "_is_file",
        "_is_file_nf",
        "_is_symlink",
        "_stat",
        "_stat_nf",
    )

    def __init__(self, entry: Any) -> None:
        self.name: str = entry.name
        self.path: str = entry.path
        self._inode: int = entry.inode()
        self._is_dir: bool = entry.is_dir()
        self._is_dir_nf: bool = entry.is_dir(follow_symlinks=False)
        self._is_file: bool = entry.is_file()
        self._is_file_nf: bool = entry.is_file(follow_symlinks=False)
        self._is_symlink: bool = entry.is_symlink()
        # stat() can fail (e.g. broken symlink); store None on failure.
        try:
            self._stat: Any = entry.stat()
        except OSError:
            self._stat = None
        try:
            self._stat_nf: Any = entry.stat(follow_symlinks=False)
        except OSError:
            self._stat_nf = None

    def inode(self) -> int:
        return self._inode

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        return self._is_dir if follow_symlinks else self._is_dir_nf

    def is_file(self, *, follow_symlinks: bool = True) -> bool:
        return self._is_file if follow_symlinks else self._is_file_nf

    def is_symlink(self) -> bool:
        return self._is_symlink

    def stat(self, *, follow_symlinks: bool = True) -> Any:
        result = self._stat if follow_symlinks else self._stat_nf
        if result is None:
            raise FileNotFoundError(self.path)
        return result

    def __fspath__(self) -> str:
        return self.path

    def __repr__(self) -> str:
        return f"<_PicklableDirEntry {self.name!r}>"


class ScanDirPatcher(
    Patcher[Any, tuple[list[_PicklableDirEntry] | None, BaseException | None]]
):
    """Patcher for ``os.scandir``.

    Like ``IteratorPatcher`` but converts each ``DirEntry`` to a
    ``_PicklableDirEntry`` so the state can be pickled by the snapshot
    mechanism.
    """

    @staticmethod
    def play(
        func: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> tuple[
        Callable[[], Any],
        tuple[list[_PicklableDirEntry] | None, BaseException | None],
    ]:
        ret: list[_PicklableDirEntry] | None = None
        ex: BaseException | None = None
        try:
            ret = [_PicklableDirEntry(e) for e in func(*args, **kwargs)]
        except Exception as err:  # noqa: BLE001
            ex = err
        state = (ret, ex)

        def run() -> Any:
            if ex is not None:
                raise ex
            return _ReplayableIterator(ret)  # type: ignore[arg-type]

        return run, state

    @staticmethod
    def replay(
        func: Callable[..., Any],
        state: tuple[list[_PicklableDirEntry] | None, BaseException | None],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        ret, ex = state
        if ex is not None:
            raise ex
        return _ReplayableIterator(ret)  # type: ignore[arg-type]


# @patch(patcher=GenericPatcher)
# def my_func():
#     pass

# example use case: patch just open() function instead of all functions on the File

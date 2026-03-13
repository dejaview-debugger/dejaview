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


@dataclass
class _LazyIteratorState:
    cached_items: list[Any]
    exhausted: bool


class _RecordingIterator:
    """Iterator that lazily records values consumed during play."""

    def __init__(self, source: Iterator[Any], state: _LazyIteratorState) -> None:
        self._source = source
        self._state = state
        self._index = 0

    def __iter__(self) -> "_RecordingIterator":
        return self

    def __next__(self) -> Any:
        if self._index < len(self._state.cached_items):
            item = self._state.cached_items[self._index]
            self._index += 1
            return item
        if self._state.exhausted:
            raise StopIteration

        try:
            item = next(self._source)
        except StopIteration:
            self._state.exhausted = True
            raise
        self._state.cached_items.append(item)
        self._index += 1
        return item

    def __enter__(self) -> "_RecordingIterator":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        close = getattr(self._source, "close", None)
        if callable(close):
            close()


class IteratorPatcher(
    Patcher[Any, tuple[_LazyIteratorState | None, BaseException | None]]
):
    """Patcher for iterator/generator-returning functions.

    During *play*, only values actually consumed by user code are recorded.
    During *replay*, a fresh
    ``_ReplayableIterator`` over the stored list is returned so callers can
    iterate again without re-executing the original function.

    .. note::

    The cached items must be picklable because the snapshot
       mechanism sends ``StateStore`` data through ``multiprocessing``
       queues.  For ``os.scandir`` (whose ``DirEntry`` objects are
       **not** picklable), use ``ScanDirPatcher`` instead.
    """

    @staticmethod
    def play(
        func: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> tuple[
        Callable[[], Any], tuple[_LazyIteratorState | None, BaseException | None]
    ]:
        state: _LazyIteratorState | None = None
        ex: BaseException | None = None
        try:
            source = iter(func(*args, **kwargs))
            state = _LazyIteratorState(cached_items=[], exhausted=False)
        except Exception as err:  # noqa: BLE001
            ex = err
        packed_state = (state, ex)

        def run() -> Any:
            if ex is not None:
                raise ex
            assert state is not None
            return _RecordingIterator(source, state)

        return run, packed_state

    @staticmethod
    def replay(
        func: Callable[..., Any],
        state: tuple[_LazyIteratorState | None, BaseException | None],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        lazy_state, ex = state
        if ex is not None:
            raise ex
        assert lazy_state is not None
        return _ReplayableIterator(lazy_state.cached_items)


@dataclass
class _ScanDirState:
    call_args: tuple[Any, ...]
    call_kwargs: dict[str, Any]
    cached_names: list[str | bytes]
    exhausted: bool


class _RecordingScanDirIterator:
    """Lazily records consumed scandir entry names during play."""

    def __init__(self, source: Any, state: _ScanDirState) -> None:
        self._source = source
        self._state = state
        self._index = 0

    def __iter__(self) -> "_RecordingScanDirIterator":
        return self

    def __next__(self) -> Any:
        try:
            entry = next(self._source)
        except StopIteration:
            self._state.exhausted = True
            raise
        self._state.cached_names.append(entry.name)
        self._index += 1
        return entry

    def __enter__(self) -> "_RecordingScanDirIterator":
        if hasattr(self._source, "__enter__"):
            self._source.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if hasattr(self._source, "__exit__"):
            self._source.__exit__(exc_type, exc_val, exc_tb)
        else:
            self.close()

    def close(self) -> None:
        close = getattr(self._source, "close", None)
        if callable(close):
            close()


class _ReplayScanDirIterator:
    """Replay iterator that yields real ``os.DirEntry`` instances."""

    def __init__(self, func: Callable[..., Any], state: _ScanDirState) -> None:
        self._func = func
        self._state = state
        self._iter: Iterator[Any] | None = None

    def _build_iter(self) -> Iterator[Any]:
        from dejaview.patching.patching import (  # noqa: PLC0415
            PatchingMode,
            set_patching_mode,
        )

        with set_patching_mode(PatchingMode.OFF):
            source = self._func(*self._state.call_args, **self._state.call_kwargs)
        try:
            entries = list(source)
        finally:
            close = getattr(source, "close", None)
            if callable(close):
                close()

        by_name: dict[str | bytes, Any] = {entry.name: entry for entry in entries}
        replay_entries = [by_name[name] for name in self._state.cached_names]
        return iter(replay_entries)

    def __iter__(self) -> "_ReplayScanDirIterator":
        return self

    def __next__(self) -> Any:
        if self._iter is None:
            self._iter = self._build_iter()
        return next(self._iter)

    def __enter__(self) -> "_ReplayScanDirIterator":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def close(self) -> None:
        pass


class ScanDirPatcher(Patcher[Any, tuple[_ScanDirState | None, BaseException | None]]):
    """Patcher for ``os.scandir``.

    Records only consumed entry names so replay can rebuild real
    ``os.DirEntry`` objects while keeping state picklable.
    """

    @staticmethod
    def play(
        func: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> tuple[
        Callable[[], Any],
        tuple[_ScanDirState | None, BaseException | None],
    ]:
        state: _ScanDirState | None = None
        ex: BaseException | None = None
        try:
            source = func(*args, **kwargs)
            state = _ScanDirState(
                call_args=tuple(args),
                call_kwargs=dict(kwargs),
                cached_names=[],
                exhausted=False,
            )
        except Exception as err:  # noqa: BLE001
            ex = err
        packed_state = (state, ex)

        def run() -> Any:
            if ex is not None:
                raise ex
            assert state is not None
            return _RecordingScanDirIterator(source, state)

        return run, packed_state

    @staticmethod
    def replay(
        func: Callable[..., Any],
        state: tuple[_ScanDirState | None, BaseException | None],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        scan_state, ex = state
        if ex is not None:
            raise ex
        assert scan_state is not None
        return _ReplayScanDirIterator(func, scan_state)


# @patch(patcher=GenericPatcher)
# def my_func():
#     pass

# example use case: patch just open() function instead of all functions on the File

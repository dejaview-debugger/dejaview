# Patching Model

DejaView patches non-deterministic functions so that their results are memoized during the initial execution ("play") and replayed from the stored values during backward stepping ("replay"). This enables time-travel debugging by ensuring the program produces identical behavior on every replay.

## How patching works

There are three patching mechanisms:

- **`p.patch(obj, "func")`** — Wraps the function with `log_results`, which records the return value on play and returns the stored value on replay. This is the primary mechanism.
- **`p.decorate(obj, "func", decorator)`** — Wraps the function with a custom decorator. Used for output muting (`mute_decorator` skips the call entirely during `PatchingMode.MUTED`).
- **`p.replace(obj, "attr", new_value)`** — Replaces an attribute with a new value, restored on cleanup. Used for module-level class/function swaps (e.g. `io.FileIO` → `_pyio.FileIO`).

There are also context-manager patches added via `p.add()`, used for module swaps (`sys.modules["datetime"] = _pydatetime`) and the Rust memory patch.

### `should_patch`

`p.patch()` accepts an optional `should_patch` predicate. When provided, the function is only memoized if `should_patch(*args, **kwargs)` returns `True`; otherwise the original function runs unpatched. The predicate must be deterministic for the same arguments.

Example: `time.localtime(seconds)` is only patched when called without arguments (i.e. it reads the clock), not when called with an explicit timestamp.

## What needs patching

A function needs patching if it is **non-deterministic** — i.e. it can return different values or produce different side effects across replays for the same arguments. Here's some examples of non-deterministic functions:

- **Time** — `time.time()`, `time.monotonic()`, etc.
- **Randomness** — `random.random()`, `random.SystemRandom.getrandbits()`
- **Process/OS state** — `os.getpid()`, `sys.getrefcount()`, `sys.getsizeof()`
- **User input** — `builtins.input()`, `sys.stdin.readline()`
- **Network** — `socket.socket.recvfrom()`, `socket.socket.sendto()`
- **Filesystem** — `open()`, file reads/writes (DejaView's `fork` does not snapshot disk)
- **Object identity** — `id()`, `hash()` for objects with identity-based hashing (handled by the Rust `_memory_patch` extension)

Although not all of these need to be patched. See next section for the details.

Some examples of functions that may seem non-deterministic but actually are:
- `os.environ` — deterministic within a process, similar to a global variable.

### The dependency principle

**If a function's non-determinism flows only through already-patched functions, it does not need its own patch.**

This is the key principle for deciding what to patch. Trace the function's implementation to find its sources of non-determinism. If all of those sources are already patched, the function will replay deterministically without its own patch.

Examples:
- `shutil.copy()` — internally calls `os.open()`, `os.read()`, `os.write()`, etc. If `os` is patched, `shutil` needs no patches.
- `time.ctime()` without arguments — calls `time.time()` internally, but via C, not through the Python `time` module. So it needs its own patch.
- `datetime.datetime.now()` — in the C `datetime` module, calls C-level clock functions directly. Solved by swapping to `_pydatetime`, which calls `time.time()` (already patched).
- `random.random()` — depends on the random seed which is normally altered during forking, but DejaView's `safe_fork` preserves the seed across forks, so no patch is needed.
- `random.SystemRandom.getrandbits()` — calls `os.urandom()` which is patched.

### C extension bypass

Many CPython stdlib modules have both a C implementation and a pure Python fallback. The C versions often call C-level syscalls directly, **bypassing Python-level patches**. The solution is to swap the C module for its pure Python equivalent that routes through patchable Python functions.

### Patching functions that return complex objects

When a patched function returns a complex object (e.g. a file handle, HTTP response, subprocess), the order of preference from most transparent to least is:

1. **Replace the non-deterministic calls used by the library's implementation** — if the library is written in Python, not C. This is the most transparent because the original classes and objects are preserved. (Example: `datetime` → `_pydatetime`, `io` → `_pyio`.)
2. **Patch methods/fields on the class of the returned object** — patch only the non-deterministic parts, keeping the original class. (Example: patching `socket.socket.recv`, `socket.socket.send`, etc.)
3. **Replace the class itself** — return a custom stand-in object on replay. This is the least transparent because it can break `isinstance` checks, `print(obj)`, `dir(obj)`, attribute access, etc. Only use this as a last resort.

Always attempt options 1 and 2 before resorting to option 3.

### Pitfalls

- **DejaView internals calling patched functions**: If pdb or DejaView's own code calls a patched function (e.g. pdb's `linecache.checkcache` calling `os.stat`), it advances the sequence counter during replay, causing divergence. Fix by wrapping the internal call with `PatchingMode.OFF`.
- **Constructor patching breaks object identity**: Patching a class constructor (e.g. `socket.socket`) with `log_results` returns the memoized object from play, not the one just constructed. Patch `__init__` instead to preserve object identity.
- **AF_UNIX sockets**: DejaView uses AF_UNIX sockets for multiprocessing IPC. Patching these breaks internal communication. Use `should_patch` to skip them.
- **Tests that are trivially deterministic**: A test that runs a deterministic program twice and asserts equal output will pass even without patching. Tests should use actually non-deterministic inputs or verify from the driver side that side effects (e.g. file writes) are suppressed on replay.

## Adding a new patch

1. Identify the non-deterministic function.
2. Trace its implementation to find the source of non-determinism.
3. If the non-determinism flows only through already-patched functions, no patch is needed. Document the reasoning and move on.
    - Not patching is better than patching if both work.
4. If it calls C-level syscalls directly, check if a pure Python fallback exists that routes through patchable Python functions.
5. Choose the least invasive and least brittle approach.
    - Prefer patching at the source of non-determinism rather than at every call site.
    - Prefer preserving original classes and objects over replacing them with stand-ins (see "Patching functions that return complex objects" above).
6. Choose the mechanism:
    - `p.patch()` for memoization
    - `p.decorate()` for custom behavior (e.g. muting)
    - `p.replace()` for attribute swaps.
6. Add an end-to-end test in `test_patch.py`.
    - Typically, running the program twice and asserting equal output is sufficient.
    - Simple functions that have simple return type, such as `time.time()`, don't need to be tested at all.

## Examples

### `shutil` — no patch needed

`shutil.copy()`, `shutil.move()`, etc. internally call `os.open()`, `os.read()`, `os.write()`, `os.stat()`, etc. All non-determinism flows through `os`, which is already patched. By the dependency principle, `shutil` needs no patches.

### `datetime` — C extension bypass, module swap

`datetime.datetime.now()` in the C `datetime` module calls C-level `time()` directly, not `time.time()`. Patching `time.time` has no effect.

Solution: swap `sys.modules["datetime"] = _pydatetime`. The pure Python `_pydatetime` calls `time.time()` (already patched) as its only source of non-determinism, so no function-level patches are needed. This works as a `sys.modules` swap because the debuggee imports `datetime` after patching, so it picks up the swapped module.

### `io` — C extension bypass, attribute replacement

`_io.FileIO.read()` calls C `read()`, not `os.read()`. The pure Python `_pyio.FileIO.read()` calls `os.read()` (patchable).

Unlike `datetime`, a simple `sys.modules` swap does not work for `io`:

- **`sys.modules["io"] = _pyio` breaks `isinstance`**: `io` defines ABC classes (`IOBase`, `TextIOBase`, etc.) that C `_io` objects are registered into. Pre-existing C objects like `sys.stdout` pass `isinstance(obj, io.TextIOBase)` but fail `isinstance(obj, _pyio.TextIOBase)`.
- **`sys.modules["_io"] = _pyio` has no effect**: `io` is imported at interpreter startup and does `from _io import FileIO, TextIOWrapper, ...`, binding direct references to C classes. Swapping `_io` in `sys.modules` afterward doesn't change these already-bound names inside `io`.

Solution: replace concrete class names inside the already-imported `io` module (`io.open`, `io.FileIO`, `io.TextIOWrapper`, etc.) with `_pyio` counterparts via `p.replace()`. ABCs are left untouched, preserving `isinstance` checks.

Caveat: code that did `from io import open` or `from io import FileIO` before patching holds a direct reference to the C version. No stdlib module does this (checked in CPython 3.12). Third-party libraries are fine unless imported by DejaView before patching.

### `sys.stdin` / `sys.stdout` / `sys.stderr` — pre-existing C objects

These are C `_io.TextIOWrapper` objects created at interpreter startup, before `patch_io()` runs. They are unaffected by the `io` module class replacement and must be patched individually:

- `sys.stdin.read`, `readline`, `readlines`, `__next__` — patched with `log_results` (memoize user input)
- `sys.stdout.write`, `sys.stderr.write` — patched with `mute_decorator` (suppress duplicate output on replay)

Note: C `TextIOWrapper.__next__` calls C-level readline directly, not `self.readline()`, so it needs a separate patch. `TextIOWrapper.writelines` routes through `self.write()`, so the mute on `write` covers it.

### `time.localtime` — conditional patching with `should_patch`

`time.localtime()` without arguments reads the clock (non-deterministic). `time.localtime(timestamp)` with an explicit timestamp is deterministic. Using `should_patch=lambda seconds=None: seconds is None` avoids unnecessary memoization for the deterministic case.

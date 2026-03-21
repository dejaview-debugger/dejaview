# DejaView

DejaView adds **time-travel debugging** to Python. It wraps `pdb` with reverse execution commands — `rstep`, `rnext`, `rreturn`, `rcontinue` — that mirror their forward counterparts. Non-deterministic operations like `time.time()` and `random.random()` are recorded and replayed, so re-executing code after a reverse command produces exactly the same results as the first time.

## Quick Start

### Prerequisites

- Python 3.12. Other Python versions are not supported.
- A Rust toolchain (`rustc`/`cargo`) and `gcc` (to compile the native extension)

### Install

Install DejaView into your project's virtual environment:

```bash
pip install "dejaview @ git+https://github.com/dejaview-debugger/dejaview"
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv pip install "dejaview @ git+https://github.com/dejaview-debugger/dejaview"
```

This pulls the source, compiles the native extension, and installs DejaView alongside your project's dependencies.

### Run

Debug a script:

```bash
python -m dejaview path/to/script.py
```

Debug a module (e.g., [Black](https://github.com/psf/black)):

```bash
python -m dejaview -m black target_file.py --diff
```

You'll land at a familiar `(Pdb)` prompt. All standard `pdb` commands work — plus the reverse commands described below.

## Debugger Commands

DejaView adds these commands on top of everything `pdb` already provides:

### Reverse execution

| Command | Aliases | Description |
|---------|---------|-------------|
| `rnext` | `rn` | Reverse `next` (step over). Move to the previous line in the current function. |
| `rstep` | `rs` | Reverse `step` (step into). Move to the previous line, entering called functions. |
| `rreturn` | `rr` | Reverse `return` (step out). Move to the call site of the current function. |
| `rcontinue` | `rc` | Reverse `continue`. Run in reverse until a breakpoint is hit. |
| `restart` | | Rewind to the beginning of the program. Breakpoints and history are preserved. |

### Variable modification

| Command | Description |
|---------|-------------|
| `setvar <name> <expr>` | Set a variable in the current scope. Supports fields (`a.x`) and subscripts (`a[2]`). Changes persist through forward replay. Only available at the current execution point — not after a reverse command. |

### Example session

Debugging a binary search that returns wrong results for some inputs:

```python
# search.py
def binary_search(arr, target):
    lo, hi = 0, len(arr)
    while lo < hi:
        mid = (lo + hi) // 2
        if arr[mid] <= target:
            lo = mid + 1
        else:
            hi = mid
    return lo

arr = [2, 5, 8, 12, 16, 23, 38, 56, 72, 91]
i = binary_search(arr, 23)
assert arr[i] == 23    # should find 23
```

```
$ python -m dejaview search.py
> search.py(2)<module>()
-> def binary_search(arr, target):
(Pdb) b 14                        # break at the assert
Breakpoint 1 at search.py:14
(Pdb) c
> search.py(14)<module>()
-> assert arr[i] == 23
(Pdb) p i, arr[i]
(6, 38)                           # i is 6 — that's arr[6]=38, not 23
(Pdb) rstep                       # reverse step into binary_search
--Return--
> search.py(10)binary_search()->6
-> return lo
(Pdb) p lo, hi
(6, 6)                            # search ended at index 6, but 23 is at index 5
(Pdb) b 6                         # set a breakpoint inside the loop
Breakpoint 2 at search.py:7
(Pdb) rc                          # reverse continue — find the last loop iteration
> search.py(6)binary_search()
-> if arr[mid] <= target:
(Pdb) p mid, arr[mid], lo, hi
(5, 23, 5, 6)                     # arr[mid] is 23 — we found the target!
(Pdb) n                           # step forward: which branch did we take?
> search.py(8)binary_search()
-> lo = mid + 1                   # bug: <= makes us go right when arr[mid] == target
```

## How Replay Works

DejaView patches standard library calls that introduce non-determinism (`time.time()`, `random.random()`, `datetime.datetime.now()`, `os.getpid()`, `id()`, `hash()`, etc.) so their return values and exceptions are recorded on the first execution and replayed on subsequent passes. The standard library should be mostly covered, but the patching is best-effort — it is designed to work for common use cases, not to be a security boundary.

If replay diverges from the original execution (e.g. due to an unpatched source of non-determinism), DejaView detects the mismatch after some delay and automatically restarts the debugging session rather than silently producing wrong results.

## Limitations

DejaView currently does not support:

- **Threading and multiprocessing** — only single-threaded programs are supported.
- **Async** (`asyncio`, `await`) — async coroutines are not handled (generators and `yield` work fine).
- **Non-deterministic native extensions beyond the standard library** — third-party native extensions may introduce non-determinism that DejaView cannot intercept.
- **Other Python versions** — only CPython 3.12 is supported.

## CLI Options

```
python -m dejaview [options] script.py [script_args...]
python -m dejaview [options] -m module [module_args...]
```

| Option | Description |
|--------|-------------|
| `-m` | Debug a module by name instead of a script file. |
| `-c <command>` | Execute a debugger command at startup (can be repeated). |
| `--snapshot-interval <N>` | Lines between snapshots (default: 1000). Lower values use more memory and slow down forward execution, but may make reverse stepping faster. |
| `-p <port>` | Connect to a VS Code debug adapter on the given port. |

## VS Code Extension

A companion VS Code extension provides a graphical debugging interface. See the [extension README](dejaview-extension/README.md) for setup instructions.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and guidelines.

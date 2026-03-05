# DejaView

DejaView layers deterministic frame counting, runtime patching, and snapshotting on top of `pdb` so you can step backwards while debugging Python code.

## Quick Start
- Requires Python 3.12+, [uv](https://docs.astral.sh/uv/) for dependency management, and a Rust toolchain (`rustc`/`cargo`) plus `gcc` to compile the native extension.
- Install dependencies and build the Rust extension with `uv sync` from the repository root.
- Launch the debugger against a script with `uv run python3 -m dejaview path/to/script.py` (sample programs live in `dejaview/tests/programs`).
- Inside the session, use the usual `pdb` commands plus `back` to restore the prior execution snapshot.

You can also check [copilot-instructions.md](.github/copilot-instructions.md) for detailed architecture and development guidelines.

## VS Code Extension
1. Open dejaview-extension folder in VS Code
2. Press `F5` to open a new window with your extension loaded.
3. In the new VS Code window from the previous step, open `dejaview/tests/programs` folder.
4. Run the script you want to debug from the by going to the "Run and Debug" tab and click the green arrow.

## Tests & Quality
- Run `uv run pytest` to exercise the test suite.
- Optional checks: `uv run ruff check .` and `uv run mypy .`.

See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidelines.

## Troubleshooting

To kill leftover processes:
```
ps aux | grep 'python3 -m dejaview' | awk '{print $2}' | xargs kill
```

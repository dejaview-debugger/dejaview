# DejaView

DejaView layers deterministic frame counting, runtime patching, and snapshotting on top of `pdb` so you can step backwards while debugging Python code.

## Quick Start
- Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/) for dependency management.
- Install dependencies with `uv sync` from the repository root.
- Launch the debugger against a script with `uv run python3 -m dejaview path/to/script.py` (sample programs live in `dejaview/tests/programs`).
- Inside the session, use the usual `pdb` commands plus `back` to restore the prior execution snapshot.

## VS Code Extension
1. Open djv-test folder in VS Code
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

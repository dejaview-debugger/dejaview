# Contributing

## Environment
- Install Python 3.12+, [uv](https://docs.astral.sh/uv/), a Rust toolchain (`rustc`/`cargo`), and `gcc`.
- Run `uv sync` once to install dependencies and compile the `_memory_patch` Rust extension.
- Run `uv run prek install` to set up pre-commit hooks.

## Workflow
1. Create a feature branch off `main` and stage only related changes.
2. Run `uv run prek` to check for style and typing issues.
4. Run `uv run pytest` to run the test suite.
5. Open a pull request that explains the change, expected behavior, and any test gaps.

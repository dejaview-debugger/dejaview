# Copilot Instructions for DejaView repository

## High Level Details

**Summary:**
DejaView is a tool that adds "step back" (time-travel debugging) capabilities to `pdb` for Python. It achieves this by layering deterministic frame counting, runtime patching, and snapshotting mechanisms. The project consists of two main components: a Python-based core debugger and a Visual Studio Code extension.

**Repository Info:**
- **Core (Python):** Located in `dejaview/`.
  - **Language:** Python 3.12+.
  - **Dependency Manager:** [uv](https://docs.astral.sh/uv/) (v0.5.0+ recommended).
  - **Type Checking:** `mypy`.
  - **Linting:** `ruff`.
  - **Testing:** `pytest`.

- **Extension (TypeScript):** Located in `dejaview-extension/`.
  - **Runtime:** Node.js (>=18.18.0 recommended).
  - **Package Manager:** `npm`.
  - **Framework:** VS Code Extension API.
  - **Build:** TypeScript (`tsc`).

## Build Instructions

**Always run these validation steps before submitting changes.**

### Python Core (`dejaview/`)

1.  **Setup Environment:**
    Ensure `uv` is installed. Run the following from the repo root to install dependencies:
    ```bash
    uv sync
    ```

2.  **Run Tests:**
    Execute the test suite using `pytest` via `uv`:
    ```bash
    uv run pytest
    ```
    *Note: Tests are located in `dejaview/tests`. Pass specific file paths to run subsets (e.g., `uv run pytest dejaview/tests/test_basic.py`).*

3.  **Linting & Type Checking:**
    Run linting and type checking validation (mirroring CI):
    ```bash
    uv run ruff check
    uv run ruff format --diff
    uv run mypy dejaview
    ```

4.  **Manual Execution:**
    To run the debugger against a target script (e.g., one in `dejaview/tests/programs`):
    ```bash
    uv run python3 -m dejaview dejaview/tests/programs/test.py
    ```

### VS Code Extension (`dejaview-extension/`)

1.  **Setup & Install:**
    Navigate to the extension directory and install dependencies:
    ```bash
    cd dejaview-extension
    npm install
    ```
    *Note: You may see `EBADENGINE` warnings if using older Node versions (<18), but the install generally succeeds.*

2.  **Compile:**
    Build the TypeScript source:
    ```bash
    npm run compile
    ```

3.  **Linting:**
    ```bash
    npm run lint
    ```

## Project Layout

### Major Architectural Elements

- **`dejaview/`**: The core Python package.
  - **`counting/`**: Implements deterministic frame counting and socket client for communication.
  - **`patching/`**: Handles runtime patching of Python internals/modules.
  - **`snapshots/`**: Implements `safe_fork` and snapshot management for state restoration.
  - **`tests/`**: Unit and integration tests.
    - **`programs/`**: Contains sample Python programs used as targets for debugger tests.

- **`dejaview-extension/`**: The VS Code extension source.
  - **`src/`**: TypeScript source code (`extension.ts`, `adapter.ts`).
  - **`package.json`**: Extension manifest and npm scripts.
  - **`tsconfig.json`**: TypeScript configuration.

- **`pyproject.toml`**: Project configuration for Python, including `uv` dependencies, `pytest`, `ruff`, and `mypy` settings.

### Key Configuration Files
- **Python:** `pyproject.toml` (Root)
- **Extension:** `dejaview-extension/package.json`, `dejaview-extension/tsconfig.json`

### Validation Pipelines
The project uses CircleCI for continuous integration. The pipeline (`.circleci/config.yml`) runs the following steps:
1.  `uv sync --dev`
2.  `uv run ruff check`
3.  `uv run ruff format --diff`
4.  `uv run mypy dejaview`
5.  `uv run pytest`

Ensure local changes pass these commands before pushing.

### Notes for Agents
- **Pathing:** When working on the extension, remember that its root is `dejaview-extension/`, but the Python code it interacts with is at the repo root.
- **Tools:** Use `uv` for all Python-related tasks. Do not try to use `pip` or `venv` directly unless specifically troubleshooting `uv` issues.
- **Trust these instructions:** If `uv` or `npm` commands fail, check for environment issues (missing `uv` binary, old `node` version) before assuming the codebase is broken.

## Recommended Implementation Flow

1.  If changing Python Core:
    - Edit files in `dejaview/`.
    - Run `uv run pytest` to verify.
    - Run `uv run ruff check` and `uv run ruff format` to lint/format.
    - Run `uv run mypy dejaview` to check types.

2.  If changing Extension:
    - Edit files in `dejaview-extension/src/`.
    - Run `npm run compile` to build.
    - Run `npm run lint` to check style.

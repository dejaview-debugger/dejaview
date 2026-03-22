#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BLACK_DIR="$SCRIPT_DIR/black"

# Download black source
mkdir -p "$BLACK_DIR"
# Note: Make sure to update test_black.py if you change the black version, since it relies on specific line numbers
curl -sL https://github.com/psf/black/archive/refs/tags/26.3.1.tar.gz | tar -xz --strip-components=1 -C "$BLACK_DIR"

cd "$BLACK_DIR"

# Create venv with Python 3.12 (required by dejaview)
uv venv --python 3.12 .venv --clear

# Install black and its runtime dependencies into the venv
uv pip install --python "$BLACK_DIR/.venv/bin/python" -e ".[d]"

# Install dejaview (triggers maturin Rust build) into the same venv
uv pip install --python "$BLACK_DIR/.venv/bin/python" -e "$REPO_ROOT"

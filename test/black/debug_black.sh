#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BLACK_DIR="$SCRIPT_DIR/black"

"$BLACK_DIR/.venv/bin/python" -m dejaview -m black "$@"

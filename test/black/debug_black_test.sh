#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

${SCRIPT_DIR}/debug_black.sh ${SCRIPT_DIR}/test.py --diff --target-version py312

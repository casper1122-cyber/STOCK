#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
exec .venv/bin/python -m stocklab serve --port "${STOCKLAB_PORT:-8000}"

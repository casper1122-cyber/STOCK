#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
if command -v uv >/dev/null 2>&1; then
  uv pip sync requirements.txt --python .venv/bin/python
else
  .venv/bin/python -m pip install -r requirements.txt
fi
.venv/bin/python -m compileall -q stocklab

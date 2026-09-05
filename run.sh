#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$ROOT_DIR/venv/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Missing virtualenv python at $VENV_PYTHON" >&2
  echo "Create it with: python3 -m venv venv && venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

exec "$VENV_PYTHON" -m uvicorn app.main:app --host 0.0.0.0 --port 8555 "$@"

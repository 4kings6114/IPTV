#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
python_bin="${PYTHON:-python3}"
if ! command -v "$python_bin" >/dev/null 2>&1; then
  echo 'Python 3.10+ was not found. Set PYTHON to its executable path.' >&2
  exit 2
fi
# Secrets are loaded literally by Python from .env, not sourced as shell code.
exec "$python_bin" telegram_bot.py "$@"

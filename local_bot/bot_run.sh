#!/bin/sh
set -eu
cd "$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
export PYTHONUTF8=1
if command -v python3 >/dev/null 2>&1; then
    exec python3 bot_run.py "$@"
elif command -v python >/dev/null 2>&1; then
    exec python bot_run.py "$@"
else
    echo 'Python 3.10+ is required: https://www.python.org/downloads/' >&2
    exit 1
fi

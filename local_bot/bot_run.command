#!/bin/sh
cd "$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)" || exit 1
sh ./bot_run.sh "$@"
status=$?
printf '\nBot stopped. Press Enter to close this window. '
read -r answer
exit "$status"

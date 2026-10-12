#!/usr/bin/env bash
# Downloaded ZIPs can also be started with: bash "Launch LayerLoom.command"
APP_DIR="$(cd "$(dirname "$0")" && pwd)" || exit 1
bash "$APP_DIR/scripts/bootstrap.sh" "$@"
STATUS=$?
if [ "$STATUS" -ne 0 ] && [ -t 0 ]; then
  printf '\nPress Return to close this window...'
  read -r _unused
fi
exit "$STATUS"

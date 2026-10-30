#!/usr/bin/env bash
# Downloaded ZIPs can also be started with: bash "Launch LayerLoom.command"
APP_DIR="$(cd "$(dirname "$0")" && pwd)" || exit 1
if [ -f "$APP_DIR/scripts/bootstrap.sh" ]; then
  bash "$APP_DIR/scripts/bootstrap.sh" "$@"
  STATUS=$?
else
  printf 'The LayerLoom folder is incomplete. Extract the entire ZIP, then open the launcher inside the extracted folder. Keep its files together.\n' >&2
  STATUS=1
fi
if [ "$STATUS" -ne 0 ] && [ -t 0 ]; then
  printf '\nPress Return to close this window...'
  read -r _unused
fi
exit "$STATUS"

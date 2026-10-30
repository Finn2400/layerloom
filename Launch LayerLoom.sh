#!/usr/bin/env bash
APP_DIR="$(cd "$(dirname "$0")" && pwd)" || exit 1
if [ ! -f "$APP_DIR/scripts/bootstrap.sh" ]; then
  printf 'The LayerLoom folder is incomplete. Extract the entire ZIP, then open the launcher inside the extracted folder. Keep its files together.\n' >&2
  exit 1
fi
exec bash "$APP_DIR/scripts/bootstrap.sh" "$@"

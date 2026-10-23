#!/usr/bin/env bash
APP_DIR="$(cd "$(dirname "$0")" && pwd)" || exit 1
exec bash "$APP_DIR/scripts/bootstrap.sh" "$@"

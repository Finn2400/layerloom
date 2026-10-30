#!/usr/bin/env bash
# No preinstalled Python, pip, Git, or shell-profile changes are needed.
set -eu
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
for required in .python-version pyproject.toml uv.lock scripts/runtime-pins.txt scripts/bootstrap.py src/layerloom/gui_app.py; do
  if [ ! -f "$APP_DIR/$required" ]; then
    printf 'The LayerLoom folder is incomplete. Extract the entire ZIP, then open the launcher inside the extracted folder. Keep its files together.\nMissing: %s\n' "$required" >&2
    exit 1
  fi
done
PINS="$APP_DIR/scripts/runtime-pins.txt"
PYTHON_VERSION="$(awk '{sub(/\r$/, "")} $1 == "python" {print $2}' "$PINS")"
UV_VERSION="$(awk '{sub(/\r$/, "")} $1 == "uv" {print $2}' "$PINS")"
SYSTEM="$(uname -s)"
ARCH="$(uname -m)"
case "$SYSTEM" in
  Darwin)
    # A Terminal running under Rosetta should still provision the native runtime.
    if [ "$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null || true)" = "1" ]; then ARCH=arm64; fi
    case "$ARCH" in
      arm64) TARGET=aarch64-apple-darwin ;;
      x86_64) TARGET=x86_64-apple-darwin ;;
      *) printf 'LayerLoom supports Apple Silicon and Intel Macs.\n' >&2; exit 1 ;;
    esac
    APP_HOME="${LAYERLOOM_HOME:-$HOME/Library/Application Support/LayerLoom}"
    ;;
  Linux)
    if [ "$ARCH" != x86_64 ]; then
      printf 'This LayerLoom launcher currently supports Linux x86_64 desktops.\n' >&2; exit 1
    fi
    TARGET=x86_64-unknown-linux-gnu
    APP_HOME="${LAYERLOOM_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/layerloom}"
    ;;
  *) printf 'Use the Windows .bat launcher, or a supported macOS/Linux desktop.\n' >&2; exit 1 ;;
esac
EXPECTED="$(awk -v target="$TARGET" '{sub(/\r$/, "")} $1 == "asset" && $2 == target {print $3}' "$PINS")"
if [ "${#EXPECTED}" -ne 64 ] || [ -z "$PYTHON_VERSION" ] || [ -z "$UV_VERSION" ]; then
  printf 'The download is missing its runtime manifest. Download a fresh LayerLoom ZIP.\n' >&2; exit 1
fi
if ! mkdir -p "$APP_HOME/tools" "$APP_HOME/logs"; then
  printf 'LayerLoom cannot create its setup folders in %s. Check folder permissions and free disk space.\n' "$APP_HOME" >&2
  exit 1
fi
LOG="$APP_HOME/logs/bootstrap-$(date +%Y%m%d-%H%M%S)-$$.log"
if ! : > "$LOG"; then
  printf 'LayerLoom cannot write its setup log in %s. Check folder permissions and free disk space.\n' "$APP_HOME/logs" >&2
  exit 1
fi
exec > >(tee -a "$LOG") 2>&1
trap 'status=$?; if [ "$status" -ne 0 ]; then printf "\nSetup stopped. Reopen the launcher to retry. Log: %s\n" "$LOG"; fi' EXIT
printf 'LayerLoom: preparing its private Python %s.\n' "$PYTHON_VERSION"
TOOL_DIR="$APP_HOME/tools/uv-$UV_VERSION-$TARGET"
UV="$TOOL_DIR/uv"
if [ ! -x "$UV" ] || ! "$UV" --version | awk -v v="$UV_VERSION" '$2 == v {ok=1} END {exit !ok}'; then
  if [ "${LAYERLOOM_OFFLINE:-0}" = 1 ]; then
    printf 'First setup needs an internet connection. Reconnect and reopen LayerLoom.\n'; exit 1
  fi
  TEMP_DIR="$(mktemp -d "$APP_HOME/tools/download.XXXXXXXX")"
  ARCHIVE="$TEMP_DIR/uv.tar.gz"
  URL="https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$TARGET.tar.gz"
  printf 'Downloading the setup tool...\n'
  if command -v curl >/dev/null 2>&1; then
    curl --fail --location --show-error --retry 2 --connect-timeout 30 "$URL" --output "$ARCHIVE"
  elif command -v wget >/dev/null 2>&1; then
    wget --timeout=30 --tries=3 --output-document="$ARCHIVE" "$URL"
  else
    printf 'Install curl or wget using your Linux software manager, then reopen LayerLoom.\n'; exit 1
  fi
  if command -v shasum >/dev/null 2>&1; then
    ACTUAL="$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')"
  elif command -v sha256sum >/dev/null 2>&1; then
    ACTUAL="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
  else
    printf 'A SHA-256 tool is required to verify the setup download.\n'; exit 1
  fi
  if [ "$ACTUAL" != "$EXPECTED" ]; then
    printf 'The setup download failed its integrity check. Reopen LayerLoom to download it again.\n'; exit 1
  fi
  tar -xzf "$ARCHIVE" -C "$TEMP_DIR"
  mkdir -p "$TOOL_DIR"
  # Atomic replacement prevents a simultaneous launch from seeing a partial binary.
  cp "$TEMP_DIR/uv-$TARGET/uv" "$TOOL_DIR/uv.new.$$"
  chmod +x "$TOOL_DIR/uv.new.$$"
  mv "$TOOL_DIR/uv.new.$$" "$UV"
fi
# Isolate Python discovery from activated venvs and user uv configuration.
unset PYTHONHOME PYTHONPATH VIRTUAL_ENV CONDA_PREFIX
while IFS='=' read -r variable _value; do
  case "$variable" in UV_*) unset "$variable" ;; esac
done < <(env)
export UV_CACHE_DIR="$APP_HOME/cache"
export UV_PYTHON_INSTALL_DIR="$APP_HOME/python"
export UV_PYTHON_INSTALL_BIN=false UV_PYTHON_INSTALL_REGISTRY=false UV_PYTHON_NO_REGISTRY=true
if [ "${LAYERLOOM_OFFLINE:-0}" = 1 ]; then export UV_OFFLINE=true; else unset UV_OFFLINE; fi
PYTHON_BIN="$("$UV" python find --system --managed-python --no-config --no-python-downloads "$PYTHON_VERSION" 2>/dev/null || true)"
if [ -z "$PYTHON_BIN" ]; then
  printf 'Installing private Python %s...\n' "$PYTHON_VERSION"
  "$UV" python install --no-config --no-bin --no-registry "$PYTHON_VERSION"
  PYTHON_BIN="$("$UV" python find --system --managed-python --no-config --no-python-downloads "$PYTHON_VERSION" 2>/dev/null || true)"
  if [ -z "$PYTHON_BIN" ]; then
    printf 'Repairing a damaged private Python installation...\n'
    "$UV" python install --no-config --no-bin --no-registry --reinstall "$PYTHON_VERSION"
    PYTHON_BIN="$("$UV" python find --system --managed-python --no-config --no-python-downloads "$PYTHON_VERSION")"
  fi
fi
"$PYTHON_BIN" -I -X utf8 -u "$APP_DIR/scripts/bootstrap.py" --home "$APP_HOME" --uv "$UV" --source "$APP_DIR" "$@"

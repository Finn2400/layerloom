#!/usr/bin/env bash

# Double-click launcher for macOS source checkouts.
# It creates/reuses .venv, installs the GUI extras when needed, runs the
# diagnostic check, and then starts the stable layerloom-gui entry point.

set -u

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$APP_DIR/.venv"

pause_for_double_click() {
  if [ -t 0 ]; then
    printf "\nPress Return to close this window..."
    read -r _unused
  fi
}

fail() {
  printf "\nLayerLoom launcher failed: %s\n" "$1" >&2
  pause_for_double_click
  exit 1
}

find_python() {
  if [ -x "$VENV_DIR/bin/python" ]; then
    printf "%s\n" "$VENV_DIR/bin/python"
    return 0
  fi

  for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done

  return 1
}

python_is_supported() {
  "$1" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
}

gui_stack_is_available() {
  "$1" - <<'PY'
import importlib.util
required = ("layerloom", "lxml", "networkx", "numpy", "scipy", "trimesh", "PyQt5", "pyvista", "pyvistaqt", "vtk")
missing = [name for name in required if importlib.util.find_spec(name) is None]
if missing:
    print("Missing packages:", ", ".join(missing))
    raise SystemExit(1)
raise SystemExit(0)
PY
}

cd "$APP_DIR" || fail "could not enter $APP_DIR"

printf "LayerLoom macOS launcher\n"
printf "========================\n"
printf "Repository: %s\n" "$APP_DIR"

PYTHON_BIN="$(find_python)" || fail "Python 3.10 or newer was not found. Install Python from https://www.python.org/downloads/ and try again."
python_is_supported "$PYTHON_BIN" || fail "LayerLoom requires Python 3.10 or newer. Found: $("$PYTHON_BIN" --version 2>&1)"

if [ ! -x "$VENV_DIR/bin/python" ]; then
  printf "\nCreating virtual environment at %s\n" "$VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR" || fail "could not create the virtual environment"
fi

PYTHON_BIN="$VENV_DIR/bin/python"

if ! gui_stack_is_available "$PYTHON_BIN"; then
  printf "\nInstalling LayerLoom GUI dependencies into .venv\n"
  "$PYTHON_BIN" -m pip install --upgrade pip || fail "pip upgrade failed"
  "$PYTHON_BIN" -m pip install -e ".[gui]" || fail "LayerLoom GUI install failed"
else
  printf "\nExisting .venv has the LayerLoom GUI stack.\n"
fi

printf "\nRunning layerloom-doctor\n"
"$VENV_DIR/bin/layerloom-doctor" || fail "layerloom-doctor found a problem"

if [ "${LAYERLOOM_LAUNCHER_DOCTOR_ONLY:-0}" = "1" ]; then
  printf "\nDoctor-only mode requested; not starting the GUI.\n"
  pause_for_double_click
  exit 0
fi

printf "\nStarting LayerLoom GUI\n"
"$VENV_DIR/bin/layerloom-gui"
STATUS=$?

if [ "$STATUS" -ne 0 ]; then
  fail "layerloom-gui exited with status $STATUS"
fi

pause_for_double_click

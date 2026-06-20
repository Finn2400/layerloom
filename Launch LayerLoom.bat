@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem Double-click launcher for Windows source checkouts.
rem It creates/reuses .venv, installs the GUI extras when needed, runs the
rem diagnostic check, and then starts the stable layerloom-gui entry point.

set "APP_DIR=%~dp0"
set "VENV_DIR=%APP_DIR%.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "FAIL_REASON="

cd /d "%APP_DIR%" || (
  set "FAIL_REASON=could not enter %APP_DIR%"
  goto fail
)

echo LayerLoom Windows launcher
echo ==========================
echo Repository: %APP_DIR%

if exist "%VENV_PY%" (
  set "PYTHON_BIN="%VENV_PY%""
) else (
  call :find_python
  if errorlevel 1 (
    set "FAIL_REASON=Python 3.10 or newer was not found. Install Python from https://www.python.org/downloads/ and try again."
    goto fail
  )

  echo.
  echo Creating virtual environment at %VENV_DIR%
  !PYTHON_BIN! -m venv "%VENV_DIR%"
  if errorlevel 1 (
    set "FAIL_REASON=could not create the virtual environment"
    goto fail
  )
  set "PYTHON_BIN="%VENV_PY%""
)

%PYTHON_BIN% -c "import layerloom, lxml, networkx, numpy, scipy, trimesh, PyQt5, pyvista, pyvistaqt, vtk" >nul 2>nul
if errorlevel 1 (
  echo.
  echo Installing LayerLoom GUI dependencies into .venv
  %PYTHON_BIN% -m pip install --upgrade pip
  if errorlevel 1 (
    set "FAIL_REASON=pip upgrade failed"
    goto fail
  )
  %PYTHON_BIN% -m pip install -e ".[gui]"
  if errorlevel 1 (
    set "FAIL_REASON=LayerLoom GUI install failed"
    goto fail
  )
) else (
  echo.
  echo Existing .venv has the LayerLoom GUI stack.
)

echo.
echo Running layerloom-doctor
%PYTHON_BIN% -m layerloom.doctor
if errorlevel 1 (
  set "FAIL_REASON=layerloom-doctor found a problem"
  goto fail
)

if "%LAYERLOOM_LAUNCHER_DOCTOR_ONLY%"=="1" (
  echo.
  echo Doctor-only mode requested; not starting the GUI.
  echo.
  pause
  exit /b 0
)

echo.
echo Starting LayerLoom GUI
%PYTHON_BIN% -m layerloom.gui_app
if errorlevel 1 (
  set "FAIL_REASON=layerloom-gui exited with an error"
  goto fail
)

echo.
pause
exit /b 0

:find_python
set "PYTHON_BIN="

py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 (
  set "PYTHON_BIN=py -3.12"
  exit /b 0
)

py -3.11 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 (
  set "PYTHON_BIN=py -3.11"
  exit /b 0
)

py -3.10 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 (
  set "PYTHON_BIN=py -3.10"
  exit /b 0
)

py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 (
  set "PYTHON_BIN=py -3"
  exit /b 0
)

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 (
  set "PYTHON_BIN=python"
  exit /b 0
)

exit /b 1

:fail
echo.
echo LayerLoom launcher failed: %FAIL_REASON%
echo.
pause
exit /b 1

@echo off
setlocal DisableDelayedExpansion
if not exist "%~dp0scripts\bootstrap.ps1" (
  echo The LayerLoom folder is incomplete. Right-click the ZIP and choose Extract All.
  echo Then open Launch LayerLoom inside the extracted folder. Keep its files together.
  pause
  exit /b 1
)
rem The policy applies only to this child process; no user settings are changed.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1" %*
if errorlevel 1 (
  echo.
  pause
  exit /b 1
)
exit /b 0

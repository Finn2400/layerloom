@echo off
setlocal DisableDelayedExpansion
rem The policy applies only to this child process; no user settings are changed.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1" %*
if errorlevel 1 (
  echo.
  pause
  exit /b 1
)
exit /b 0

@echo off
setlocal DisableDelayedExpansion
if not exist "%~dp0scripts\bootstrap.ps1" (
  echo The LayerLoom folder is incomplete. Right-click the ZIP and choose Extract All.
  echo Then open Launch LayerLoom inside the extracted folder. Keep its files together.
  pause
  exit /b 1
)
rem PowerShell 7 may pass incompatible modules through cmd/Python to Windows
rem PowerShell. Let the child reconstruct its own standard module search path.
rem setlocal keeps this and the execution policy from changing user settings.
set "PSModulePath="
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1" %*
if errorlevel 1 (
  echo.
  pause
  exit /b 1
)
exit /b 0

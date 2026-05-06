@echo off
:: Double-click wrapper for install-windows.ps1.
:: Bypasses the default ExecutionPolicy restriction for THIS run only,
:: without modifying user/system policy. Safe — the .ps1 only runs
:: while this .bat is open.

setlocal
set SCRIPT_DIR=%~dp0
set PS1=%SCRIPT_DIR%install-windows.ps1

if not exist "%PS1%" (
  echo install-windows.ps1 not found alongside this .bat. Aborting.
  pause
  exit /b 1
)

echo.
echo Launching PowerShell installer...
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%"

set EXITCODE=%ERRORLEVEL%
echo.
echo Installer exited with code %EXITCODE%.
echo.
pause
exit /b %EXITCODE%

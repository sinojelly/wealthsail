@echo off
setlocal DisableDelayedExpansion
pushd "%~dp0"
if errorlevel 1 (
    echo ERROR: Cannot open the project directory.
    pause
    exit /b 1
)

echo WealthSail local launcher - Windows PostgreSQL, API and frontend.
echo First startup may take a few minutes to prepare dependencies and PostgreSQL.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-local.ps1" %*
set "WS_LOCAL_EXIT_CODE=%ERRORLEVEL%"
popd

if not "%WS_LOCAL_EXIT_CODE%"=="0" (
    echo.
    echo ERROR: Local launcher exited with code %WS_LOCAL_EXIT_CODE%.
    echo See docs\26.Windows-local-development.md for setup and troubleshooting.
    pause
)
exit /b %WS_LOCAL_EXIT_CODE%

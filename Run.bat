@echo off
REM ============================================================
REM  DiskInventory launcher
REM  Double-click to run a safe Report-mode scan.
REM  Pass args to override: Run.bat -Mode Auto -Prompt
REM                        Run.bat -Restore out\xyz\actions-journal.jsonl -Apply
REM                        Run.bat -PurgeQuarantine
REM ============================================================

setlocal
cd /d "%~dp0"

REM Use Windows PowerShell 5.1 (always present on Win10/11).
REM -ExecutionPolicy Bypass only affects this process, not the system.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Invoke-Inventory.ps1" %*
set RC=%ERRORLEVEL%

echo.
echo ============================================================
echo  DiskInventory finished with exit code %RC%.
echo  Reports are under out\ inside this folder.
echo ============================================================
echo.
pause
endlocal & exit /b %RC%

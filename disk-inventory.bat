@echo off
REM disk-inventory.bat - Windows launcher for DiskInventory v2.0.
REM Resolves to the script directory, then exec's python on disk-inventory.py.
REM Stays open at the end so the user can see the output.

setlocal

REM %~dp0 ends with a backslash; strip it so quoted paths don't get broken.
set HERE=%~dp0
if "%HERE:~-1%"=="\" set HERE=%HERE:~0,-1%

pushd "%HERE%" >nul 2>&1

where python >nul 2>nul
if %ERRORLEVEL%==0 (
    python "%HERE%\disk-inventory.py" %*
) else (
    where python3 >nul 2>nul
    if %ERRORLEVEL%==0 (
        python3 "%HERE%\disk-inventory.py" %*
    ) else (
        echo [error] Python 3 is required. Install from https://python.org and ensure python is on PATH.
        popd
        exit /b 127
    )
)

set RC=%ERRORLEVEL%
popd >nul 2>&1

REM If the parent shell gave us a console (running from PowerShell/cmd),
REM pause so the output stays visible. Otherwise exit cleanly.
if "%DISKINVENTORY_NOPAUSE%"=="" pause
exit /b %RC%

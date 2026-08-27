@echo off
REM disk-inventory.bat — Windows launcher for DiskInventory v2.0
REM Mirrors disk-inventory.sh but uses Windows path resolution.

setlocal
set HERE=%~dp0
pushd "%HERE%"
where python >nul 2>nul
if %ERRORLEVEL%==0 (
    python "%HERE%disk-inventory.py" %*
) else (
    where python3 >nul 2>nul
    if %ERRORLEVEL%==0 (
        python3 "%HERE%disk-inventory.py" %*
    ) else (
        echo [error] Python 3 is required. Install from https://python.org and ensure `python` is on PATH.
        exit /b 127
    )
)
set RC=%ERRORLEVEL%
popd
exit /b %RC%

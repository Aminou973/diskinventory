@echo off
REM disk-inventory.bat - Windows launcher for DiskInventory v3.0.
REM
REM  -  UTF-8 codepage + PYTHONIOENCODING so non-ASCII paths print cleanly.
REM  -  Resolves to the script directory (with trailing-backslash quirk fixed).
REM  -  Prefers `python`, then `python3`, then the Windows launcher `py -3`.
REM  -  Guards against Python < 3.8 with a friendly error instead of a
REM     traceback.
REM  -  Pauses only when launched from cmd.exe (skip pause in PowerShell
REM     terminals / Windows Terminal — those already keep the window open).
REM
REM  Honors DISKINVENTORY_NOPAUSE=1 to suppress the pause completely
REM  (CI / non-interactive).

setlocal ENABLEEXTENSIONS
set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"

REM Switch the console to UTF-8 so non-ASCII characters print correctly.
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"

pushd "%HERE%" >nul 2>&1
if errorlevel 1 (
    echo [error] cannot pushd to %HERE%
    popd >nul 2>&1
    exit /b 127
)

set "PY="
where python >nul 2>nul
if not errorlevel 1 (
    set "PY=python"
) else (
    where python3 >nul 2>nul
    if not errorlevel 1 (
        set "PY=python3"
    ) else (
        where py >nul 2>nul
        if not errorlevel 1 (
            set "PY=py -3"
        ) else (
            echo [error] Python 3 is required.
            echo [error] Install from https://python.org and ensure it is on PATH.
            popd >nul 2>&1
            exit /b 127
        )
    )
)

REM Friendly Python-version check (the engine does its own check too,
REM but failing here saves the user 5 seconds of stack trace).
for /f "tokens=1,2" %%v in ('%PY% -c "import sys; print(sys.version_info[0], sys.version_info[1])"') do (
    set "PY_MAJOR=%%v"
    set "PY_MINOR=%%w"
)
if "%PY_MAJOR%" LSS "3" goto :python_too_old
if "%PY_MAJOR%"=="3" if "%PY_MINOR%" LSS "8" goto :python_too_old

%PY% "%HERE%\disk-inventory.py" %*
set "RC=%ERRORLEVEL%"

popd >nul 2>&1

REM Pause only from cmd.exe — PowerShell and Windows Terminal already keep
REM the console window alive. Honor DISKINVENTORY_NOPAUSE=1 as the override.
if "%DISKINVENTORY_NOPAUSE%"=="1" goto :nopause
if "%TERM_PROGRAM%"=="" if "%PSExecutionPolicy%"=="" if "%WT_SESSION%"=="" (
    if "%DISKINVENTORY_NOPAUSE%"=="" pause
)
:nopause
exit /b %RC%

:python_too_old
echo [error] Python 3.8 or newer is required (found %PY_MAJOR%.%PY_MINOR%).
echo [error] Install Python 3.8+ from https://python.org.
popd >nul 2>&1
exit /b 2

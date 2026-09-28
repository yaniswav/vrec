@echo off
setlocal
cd /d "%~dp0..\.."

where py >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install it first, for example:
    echo   winget install Python.Python.3.12
    pause
    exit /b 1
)

py -m pip install --upgrade -e .
pause

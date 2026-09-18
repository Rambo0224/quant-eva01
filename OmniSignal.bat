@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\start_dashboard.ps1"
if errorlevel 1 (
    echo.
    echo OmniSignal startup failed.
    pause
    exit /b %errorlevel%
)

@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "QUANT_LAUNCH_PYTHON=%~dp0.venv-win\Scripts\python.exe"
if not exist "%QUANT_LAUNCH_PYTHON%" set "QUANT_LAUNCH_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%QUANT_LAUNCH_PYTHON%" (
    echo Project Python environment missing. Install project dependencies first.
    pause
    exit /b 1
)
set "PYTHONUTF8=1"
"%QUANT_LAUNCH_PYTHON%" -m app.launcher %*
set "QUANT_LAUNCH_EXIT=%errorlevel%"
if not "%QUANT_LAUNCH_EXIT%"=="0" pause
exit /b %QUANT_LAUNCH_EXIT%

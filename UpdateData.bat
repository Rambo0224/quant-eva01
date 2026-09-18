@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0update_data.ps1" %*
exit /b %errorlevel%

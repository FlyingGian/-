@echo off
setlocal
cd /d "%~dp0"
echo This removes only the local .venv. User data and configuration are preserved.
if exist ".venv" rmdir /s /q ".venv"
echo Finished. data, configs, logs and reports were preserved.
pause

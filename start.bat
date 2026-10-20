@echo off
chcp 65001 >nul 2>&1
setlocal
cd /d "%~dp0"
for %%V in (3.12 3.13 3) do (
  py -%%V -c "import sys,struct;raise SystemExit(not ((3,12)<=sys.version_info[:2]<(3,14) and struct.calcsize('P')==8))" >nul 2>&1
  if not errorlevel 1 (
    py -%%V bootstrap.py %*
    if errorlevel 1 (
      pause
      exit /b 1
    )
    exit /b 0
  )
)
for %%P in (python python3) do (
  %%P -c "import sys,struct;raise SystemExit(not ((3,12)<=sys.version_info[:2]<(3,14) and struct.calcsize('P')==8))" >nul 2>&1
  if not errorlevel 1 (
    %%P bootstrap.py %*
    if errorlevel 1 (
      pause
      exit /b 1
    )
    exit /b 0
  )
)
for %%H in (HKCU HKLM) do (
  for %%V in (3.13 3.12) do (
    for /f "tokens=2,*" %%A in ('reg query "%%H\Software\Python\PythonCore\%%V\InstallPath" /ve 2^>nul ^| find "REG_SZ"') do (
      "%%Bpython.exe" bootstrap.py --find %*
      if errorlevel 1 (
        pause
        exit /b 1
      )
      exit /b 0
    )
  )
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher_help.ps1"
echo Python 3.12 or 3.13 64-bit is required.
echo Install Python Launcher or enable Add to PATH.
echo https://www.python.org/downloads/windows/
pause
exit /b 2

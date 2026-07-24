@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  ".venv\Scripts\python.exe" "start_all_windows.py" status
) else (
  ".venv\Scripts\python.exe" "start_all_windows.py" %*
)
endlocal

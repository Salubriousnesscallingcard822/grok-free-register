@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

title GFR One-Click Start
echo ============================================================
echo  Grok Free Register  One-Click Start
echo  Already-running components will be SKIPPED
echo  Dir: %CD%
echo ============================================================
echo.

set "PY=%CD%\.venv\Scripts\python.exe"
set "LOGDIR=%CD%\logs"
set "HTTP_PROXY=http://127.0.0.1:7897"
set "HTTPS_PROXY=http://127.0.0.1:7897"
set "ALL_PROXY=http://127.0.0.1:7897"
set "CLOAKBROWSER_CACHE_DIR=%CD%\.cloakbrowser"
set "COUNTPS=%CD%\scripts\count-python-match.ps1"

if not exist "%PY%" (
  echo [x] Missing venv python:
  echo     %PY%
  goto :END
)
if not exist "%COUNTPS%" (
  echo [x] Missing helper: %COUNTPS%
  goto :END
)

if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1
if not exist "%CD%\keys" mkdir "%CD%\keys" >nul 2>&1
if not exist "%CD%\auth-local\authenticated" mkdir "%CD%\auth-local\authenticated" >nul 2>&1
if not exist "%CD%\auth-local\claimed" mkdir "%CD%\auth-local\claimed" >nul 2>&1

echo [*] Checking running components...
call :COUNT_MATCH "grok_register\.register" REG_N
call :COUNT_MATCH "auth_pathb_daemon|device_flow_browser_complete" AUTH_N
call :COUNT_MATCH "import_authenticated_to_grok2api|_import_watch" IMP_N
call :COUNT_MATCH "grok_register\.email_server" MAIL_N

echo     register : !REG_N!
echo     pathb    : !AUTH_N!
echo     import   : !IMP_N!
echo     email    : !MAIL_N!
echo.

if !MAIL_N! GTR 0 (
  echo [skip] email already running
) else (
  echo [start] email_server :8088
  start "gfr-email" /MIN "%PY%" -u -m grok_register.email_server --domain yanqiudesu.top --port 8088 1>>"%LOGDIR%\oneclick-email.out.log" 2>>"%LOGDIR%\oneclick-email.err.log"
  call :SLEEP 1
)

if !REG_N! GTR 0 (
  echo [skip] register already running
) else (
  echo [start] grok_register.register
  start "gfr-register" /MIN "%PY%" -u -m grok_register.register 1>>"%LOGDIR%\oneclick-register.out.log" 2>>"%LOGDIR%\oneclick-register.err.log"
  call :SLEEP 1
)

if !AUTH_N! GTR 0 (
  echo [skip] pathb auth already running
) else (
  set "SRC="
  if exist "%CD%\auth-local\source-snapshot.jsonl" set "SRC=%CD%\auth-local\source-snapshot.jsonl"
  if not defined SRC if exist "%CD%\keys\auth-sessions.jsonl" set "SRC=%CD%\keys\auth-sessions.jsonl"
  if not defined SRC (
    echo [!] no session source, skip auth
    echo     need auth-local\source-snapshot.jsonl or keys\auth-sessions.jsonl
  ) else (
    echo [start] pathb auth
    echo         source=!SRC!
    start "gfr-pathb-auth" /MIN "%PY%" -u "%CD%\scripts\auth_pathb_daemon.py" --source-file "!SRC!" --state-file "%CD%\keys\pathb-auth-done.txt" --browser-timeout 120 --poll-timeout 180 --idle-sleep 20 --fail-sleep 8 --scan-window 500 1>>"%LOGDIR%\oneclick-auth.out.log" 2>>"%LOGDIR%\oneclick-auth.err.log"
    call :SLEEP 1
  )
)

if exist "%CD%\scripts\import_authenticated_to_grok2api.py" (
  if !IMP_N! GTR 0 (
    echo [skip] import already running
  ) else (
    set "CRED="
    if exist "%CD%\keys\.credentials" set "CRED=%CD%\keys\.credentials"
    if not defined CRED if exist "%CD%\..\grok-import\.credentials" set "CRED=%CD%\..\grok-import\.credentials"
    if defined CRED (
      echo [start] one-shot import to grok2api
      "%PY%" -u "%CD%\scripts\import_authenticated_to_grok2api.py" --auth-dir "%CD%\auth-local\authenticated" --state-file "%CD%\keys\g2a-imported-subs.txt" --batch 50 --credentials "!CRED!" 1>>"%LOGDIR%\oneclick-import.out.log" 2>>"%LOGDIR%\oneclick-import.err.log"
      echo [+] import finished, see logs\oneclick-import.out.log
    ) else (
      echo [!] missing keys\.credentials, skip import
    )
  )
) else (
  echo [!] import script missing, skip
)

echo.
echo ============================================================
echo  Final status
echo ============================================================
call :COUNT_MATCH "grok_register\.register" REG_N
call :COUNT_MATCH "auth_pathb_daemon|device_flow_browser_complete" AUTH_N
call :COUNT_MATCH "import_authenticated_to_grok2api|_import_watch" IMP_N
call :COUNT_MATCH "grok_register\.email_server" MAIL_N

set AUTHJSON=0
for %%F in ("%CD%\auth-local\authenticated\xai-*.json") do set /a AUTHJSON+=1 >nul

echo     register : !REG_N!
echo     pathb    : !AUTH_N!
echo     import   : !IMP_N!
echo     email    : !MAIL_N!
echo     tokens   : !AUTHJSON!
echo.
echo Logs: %LOGDIR%
echo   oneclick-register.out.log / .err.log
echo   oneclick-auth.out.log / .err.log
echo   oneclick-email.out.log / .err.log
echo   oneclick-import.out.log / .err.log
echo.
echo Stop all:  .venv\Scripts\python.exe start_all_windows.py stop
echo ============================================================

:END
echo.
echo Press any key to close...
pause >nul
endlocal
exit /b 0

:COUNT_MATCH
set "PAT=%~1"
set "%~2=0"
for /f %%P in ('powershell -NoProfile -ExecutionPolicy Bypass -File "%COUNTPS%" -Pattern "%PAT%"') do set "%~2=%%P"
goto :eof

:SLEEP
powershell -NoProfile -Command "Start-Sleep -Seconds %~1" >nul 2>&1
goto :eof

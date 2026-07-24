# Independent pipeline launcher for grok-free-register-main
# Does NOT modify main turbo/adaptive scripts or running xai_enroller.
#
# Usage:
#   .\start-pipeline.ps1 status
#   .\start-pipeline.ps1 register
#   .\start-pipeline.ps1 sync
#   .\start-pipeline.ps1 phone-auth
#   .\start-pipeline.ps1 all
#   .\start-pipeline.ps1 stop [-What register|sync|phone-auth|all]
#
param(
  [Parameter(Position=0)]
  [ValidateSet("status","register","sync","phone-auth","all","stop","help")]
  [string]$Action = "status",

  [ValidateSet("register","sync","phone-auth","all")]
  [string]$What = "all",

  [int]$RegisterTarget = 0,          # 0 = unlimited (uses .env TARGET if 0)
  [string]$Proxy = "",               # empty = use .env / default 127.0.0.1:7897
  [int]$PhoneWorkers = 3,
  [double]$SyncInterval = 8,
  [switch]$ForceRegister             # kill existing grok_register.register before start
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

$Py = Join-Path $Root ".venv\Scripts\python.exe"
$AdbDefault = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\Google.PlatformTools_Microsoft.Winget.Source_8wekyb3d8bbwe\platform-tools\adb.exe"
$Adb = if (Test-Path $AdbDefault) { $AdbDefault } else { "adb" }
$SshKey = Join-Path $env:USERPROFILE ".ssh\id_ed25519_termux"
$Logs = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $Logs, (Join-Path $Root "keys") | Out-Null

function Write-Info([string]$msg) { Write-Host "[*] $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg) { Write-Host "[+] $msg" -ForegroundColor Green }
function Write-Warn([string]$msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }
function Write-Err([string]$msg) { Write-Host "[x] $msg" -ForegroundColor Red }

function Get-PythonMatches([string]$pattern) {
  @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and ($_.CommandLine -match $pattern) })
}

function Ensure-Tunnels {
  & $Adb reverse tcp:8000 tcp:8000 2>$null | Out-Null
  & $Adb reverse tcp:7897 tcp:7897 2>$null | Out-Null
  & $Adb reverse tcp:1080 tcp:1080 2>$null | Out-Null
  & $Adb forward tcp:8022 tcp:8022 2>$null | Out-Null
}

function Invoke-Phone([string]$cmd) {
  if (-not (Test-Path $SshKey)) { throw "missing ssh key: $SshKey" }
  Ensure-Tunnels
  $args = @(
    "-i", $SshKey,
    "-o", "StrictHostKeyChecking=no",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-p", "8022", "127.0.0.1", $cmd
  )
  & ssh @args
}

function Get-ProxyUrl {
  if ($Proxy) { return $Proxy }
  if ($env:HTTP_PROXY) { return $env:HTTP_PROXY }
  # read .env
  $envFile = Join-Path $Root ".env"
  if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
      if ($line -match '^\s*HTTP_PROXY\s*=\s*(.+)\s*$') { return $Matches[1].Trim() }
    }
  }
  return "http://127.0.0.1:7897"
}

function Show-Status {
  Write-Host ""
  Write-Host "=== Pipeline Status (independent launcher) ===" -ForegroundColor White
  Write-Host "root: $Root"

  $reg = Get-PythonMatches 'grok_register\.register'
  $authPc = Get-PythonMatches 'xai_enroller\.service'
  $sync = Get-PythonMatches 'phone_keys_sync\.py'
  $launcherReg = Get-PythonMatches 'tempmail_main|start-pipeline|grok_register\.register'

  Write-Host ("register workers : {0}" -f $reg.Count)
  if ($reg.Count -gt 0) {
    $reg | ForEach-Object { Write-Host ("  pid={0}" -f $_.ProcessId) }
  }
  Write-Host ("pc auth enroller : {0}" -f $authPc.Count)
  if ($authPc.Count -gt 0) {
    $authPc | ForEach-Object { Write-Host ("  pid={0}  (main auth - not managed by stop unless -What all + force)" -f $_.ProcessId) }
  }
  Write-Host ("phone keys sync  : {0}" -f $sync.Count)
  if ($sync.Count -gt 0) {
    $sync | ForEach-Object { Write-Host ("  pid={0}" -f $_.ProcessId) }
  }

  $sessions = Join-Path $Root "keys\auth-sessions.jsonl"
  $accounts = Join-Path $Root "keys\accounts.txt"
  $authLocal = Join-Path $Root "auth-local\authenticated"
  if (Test-Path $sessions) {
    $sz = (Get-Item $sessions).Length
    Write-Host ("sessions file    : {0:N0} bytes" -f $sz)
  } else { Write-Host "sessions file    : missing" }
  if (Test-Path $accounts) {
    $n = (Get-Content $accounts | Measure-Object -Line).Lines
    Write-Host ("accounts.txt     : {0} lines" -f $n)
  }
  if (Test-Path $authLocal) {
    $n = @(Get-ChildItem $authLocal -Filter "xai-*.json" -File -ErrorAction SilentlyContinue).Count
    Write-Host ("pc auth-local    : {0} xai json" -f $n)
  }

  # recent register log tails
  foreach ($f in @("tempmail_main.out.log","register-tool.out.log","phone_sync.log")) {
    $p = Join-Path $Logs $f
    if (Test-Path $p) {
      Write-Host ("log {0} : {1}" -f $f, (Get-Item $p).LastWriteTime)
    }
  }

  Write-Host "--- phone ---"
  try {
    $dev = & $Adb devices 2>$null | Select-String "device$"
    if (-not $dev) { Write-Warn "no adb device"; return }
    Ensure-Tunnels
    $out = Invoke-Phone "echo PHONE_OK; date; ps -ef 2>/dev/null | grep -E 'phone_xai_auth' | grep -v grep | wc -l; pgrep -af phone_xai 2>/dev/null | wc -l; ls ~/gfr-phone/work/authenticated 2>/dev/null | wc -l; wc -c ~/gfr-phone/host-pack/gfr-host/keys/auth-sessions.jsonl 2>/dev/null | awk '{print `$1}'; tail -n 3 ~/gfr-phone/logs/phone_xai_auth_run.log 2>/dev/null || tail -n 3 ~/gfr-phone/logs/phone_xai_w0.log 2>/dev/null"
    $lines = @($out)
    Write-Host ($lines -join "`n")
  } catch {
    Write-Warn ("phone status failed: {0}" -f $_.Exception.Message)
  }
  Write-Host ""
}

function Start-Register {
  if (-not (Test-Path $Py)) { throw "missing venv python: $Py" }
  $existing = Get-PythonMatches 'grok_register\.register'
  if ($existing.Count -gt 0) {
    if (-not $ForceRegister) {
      Write-Warn ("register already running PIDs={0}. Use -ForceRegister to replace." -f (($existing | ForEach-Object ProcessId) -join ','))
      return
    }
    foreach ($p in $existing) {
      Write-Warn "stopping register pid=$($p.ProcessId)"
      Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
  }

  $proxyUrl = Get-ProxyUrl
  $env:HTTP_PROXY = $proxyUrl
  $env:HTTPS_PROXY = $proxyUrl
  $env:ALL_PROXY = $proxyUrl
  $env:CLOAKBROWSER_CACHE_DIR = Join-Path $Root ".cloakbrowser"
  $env:EMAIL_MODE = "tempmail"

  # keep .env EMAIL_MODE=tempmail without rewriting other keys
  $envFile = Join-Path $Root ".env"
  if (Test-Path $envFile) {
    $raw = Get-Content $envFile
    $raw = $raw | ForEach-Object {
      if ($_ -match '^\s*EMAIL_MODE\s*=') { "EMAIL_MODE=tempmail" } else { $_ }
    }
    if (-not ($raw -match '^\s*EMAIL_MODE\s*=')) { $raw = @("EMAIL_MODE=tempmail") + $raw }
    $raw | Set-Content $envFile -Encoding UTF8
  }

  $out = Join-Path $Logs "pipeline-register.out.log"
  $err = Join-Path $Logs "pipeline-register.err.log"
  $pidf = Join-Path $Logs "pipeline-register.pid"
  $args = @("-u", "-m", "grok_register.register")
  if ($RegisterTarget -gt 0) { $args += @("--target", "$RegisterTarget") }

  Write-Info "starting tempmail register proxy=$proxyUrl target=$RegisterTarget"
  $p = Start-Process -FilePath $Py -ArgumentList $args -WorkingDirectory $Root `
    -RedirectStandardOutput $out -RedirectStandardError $err -PassThru -WindowStyle Hidden
  $p.Id | Set-Content $pidf
  Write-Ok "register started pid=$($p.Id)"
  Write-Host "  out: $out"
  Write-Host "  err: $err"
  Start-Sleep -Seconds 2
  if (Test-Path $out) { Get-Content $out -Tail 15 }
  if (Test-Path $err) {
    $e = Get-Content $err -Tail 10 -ErrorAction SilentlyContinue
    if ($e) { Write-Warn "stderr:"; $e | ForEach-Object { Write-Host "  $_" } }
  }
}

function Start-Sync {
  $existing = Get-PythonMatches 'phone_keys_sync\.py'
  if ($existing.Count -gt 0) {
    Write-Warn ("sync already running PIDs={0}" -f (($existing | ForEach-Object ProcessId) -join ','))
    return
  }
  $script = Join-Path $Logs "phone_keys_sync.py"
  if (-not (Test-Path $script)) { throw "missing $script" }
  $pyCmd = Get-Command python -ErrorAction SilentlyContinue
  if ($pyCmd) { $hostPy = $pyCmd.Source } else { $hostPy = "python" }
  $out = Join-Path $Logs "pipeline-sync.out.log"
  $err = Join-Path $Logs "pipeline-sync.err.log"
  $pidf = Join-Path $Logs "pipeline-sync.pid"
  $env:SYNC_INTERVAL = "$SyncInterval"
  $env:ADB = $Adb
  Write-Info "starting phone keys sync every ${SyncInterval}s"
  $p = Start-Process -FilePath $hostPy -ArgumentList @("-u", $script) -WorkingDirectory $Root `
    -RedirectStandardOutput $out -RedirectStandardError $err -PassThru -WindowStyle Hidden
  $p.Id | Set-Content $pidf
  Write-Ok "sync started pid=$($p.Id)"
}

function Start-PhoneAuth {
  Write-Info "starting phone auth workers=$PhoneWorkers"
  Ensure-Tunnels
  # push fast auth if present in pack
  $fast = Join-Path $env:USERPROFILE ".." # noop
  $packFast = "E:\download\claude\openai-Register\logs\phone_gfr_main_pack\bin\phone_xai_auth_fast.py"
  $startSh = "E:\download\claude\openai-Register\logs\phone_gfr_main_pack\bin\start_auth_workers.sh"
  if (Test-Path $packFast) {
    & $Adb push $packFast /sdcard/phone_xai_auth_fast.py 2>$null | Out-Null
  }
  if (Test-Path $startSh) {
    & $Adb push $startSh /sdcard/start_auth_workers.sh 2>$null | Out-Null
  }
  # also push existing proven script as fallback
  $cmd = @"
set -e
mkdir -p ~/gfr-phone/bin ~/gfr-phone/logs ~/gfr-phone/work/authenticated
if [ -f /sdcard/phone_xai_auth_fast.py ]; then
  cp /sdcard/phone_xai_auth_fast.py ~/gfr-phone/bin/phone_xai_auth_fast.py
  sed -i 's/\r`$//' ~/gfr-phone/bin/phone_xai_auth_fast.py
  chmod +x ~/gfr-phone/bin/phone_xai_auth_fast.py
fi
if [ -f /sdcard/start_auth_workers.sh ]; then
  cp /sdcard/start_auth_workers.sh ~/gfr-phone/bin/start_auth_workers.sh
  sed -i 's/\r`$//' ~/gfr-phone/bin/start_auth_workers.sh
  chmod +x ~/gfr-phone/bin/start_auth_workers.sh
fi
# prefer fast script; fallback to existing phone_xai_auth_http.py
export LD_LIBRARY_PATH="`${PREFIX:-/data/data/com.termux/files/usr}/lib:`${LD_LIBRARY_PATH:-}"
export HTTP_PROXY=http://127.0.0.1:7897
export HTTPS_PROXY=http://127.0.0.1:7897
export GROK2API_ADMIN=http://127.0.0.1:8000/api/admin/v1
export PHONE_AUTH_SLEEP_OK=2.5
export PHONE_AUTH_SLEEP_FAIL=6
export PHONE_AUTH_IMPORT_EVERY=5
export PHONE_AUTH_RELOAD_EVERY=15
export PHONE_AUTH_WORKER_TOTAL=$PhoneWorkers
cd ~/gfr-phone
# stop only phone_xai workers (not unrelated python)
pkill -f phone_xai_auth 2>/dev/null || true
sleep 1
source .venv/bin/activate 2>/dev/null || true
SCRIPT=bin/phone_xai_auth_fast.py
if [ ! -f "`$SCRIPT" ]; then SCRIPT=bin/phone_xai_auth_http.py; fi
for i in `$(seq 0 `$((PHONE_AUTH_WORKER_TOTAL-1))); do
  export PHONE_AUTH_WORKER_ID=`$i
  nohup python -u "`$SCRIPT" > logs/phone_xai_w`${i}.log 2>&1 &
  echo `$! > logs/phone_xai_w`${i}.pid
  echo started_worker_`$i=`$(cat logs/phone_xai_w`${i}.pid)
done
ps -A | grep phone_xai_auth | grep -v grep | wc -l
"@
  $out = Invoke-Phone $cmd
  Write-Host $out
  Write-Ok "phone auth launch requested"
}

function Stop-Pipeline {
  param([string]$Target = "all")
  Write-Info "stop target=$Target"

  if ($Target -in @("register","all")) {
    $regs = Get-PythonMatches 'grok_register\.register'
    # only stop those launched under this project path if possible
    foreach ($p in $regs) {
      if ($p.CommandLine -match [regex]::Escape($Root) -or $ForceRegister -or $Target -eq "register") {
        Write-Warn "stop register pid=$($p.ProcessId)"
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
      }
    }
  }
  if ($Target -in @("sync","all")) {
    foreach ($p in (Get-PythonMatches 'phone_keys_sync\.py')) {
      Write-Warn "stop sync pid=$($p.ProcessId)"
      Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
  }
  if ($Target -in @("phone-auth","all")) {
    try {
      Ensure-Tunnels
      $out = Invoke-Phone "pkill -f phone_xai_auth 2>/dev/null || true; echo phone_auth_stopped; (ps -ef 2>/dev/null || true) | grep phone_xai | grep -v grep | wc -l"
      Write-Host $out
    } catch {
      Write-Warn "phone stop failed: $($_.Exception.Message)"
    }
  }
  # NOTE: never auto-kill xai_enroller.service (main PC auth) unless user explicitly wants - not included
  Write-Ok "stop done (xai_enroller main auth left untouched)"
}

switch ($Action) {
  "help" {
    Write-Host @"
start-pipeline.ps1 鈥?independent launcher (safe for main tools)

  status                 Show PC + phone status
  register               Start tempmail register (single instance)
  sync                   Start PC->phone keys sync daemon
  phone-auth             Start/restart phone xAI auth workers
  all                    register + sync + phone-auth
  stop [-What X]         Stop register|sync|phone-auth|all
                         (does NOT stop main xai_enroller.service)

Examples:
  .\start-pipeline.ps1 status
  .\start-pipeline.ps1 all
  .\start-pipeline.ps1 register -RegisterTarget 50
  .\start-pipeline.ps1 stop -What register
  .\start-pipeline.ps1 phone-auth -PhoneWorkers 3
"@
  }
  "status" { Show-Status }
  "register" { Start-Register }
  "sync" { Start-Sync }
  "phone-auth" { Start-PhoneAuth }
  "all" {
    Start-Register
    Start-Sync
    Start-PhoneAuth
    Start-Sleep -Seconds 2
    Show-Status
  }
  "stop" { Stop-Pipeline -Target $What }
}

# --- accounts.x.ai browser OAuth complete (optional helper) ---
# .venv\Scripts\python.exe scripts\device_flow_browser_complete.py `
#   --source-file auth-local\source-snapshot.jsonl --source-index 0 --count 1
# .venv\Scripts\python.exe scripts\export_authenticated_json.py --from-jsonl keys\oauth_credentials.jsonl
# output: auth-local\authenticated\xai-*.json

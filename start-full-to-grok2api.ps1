# One-click: register -> browser OAuth (accounts.x.ai) -> authenticated JSON -> grok2api import
#
# Usage:
#   .\start-full-to-grok2api.ps1                 # full chain (default)
#   .\start-full-to-grok2api.ps1 all
#   .\start-full-to-grok2api.ps1 register-only -RegisterTarget 20
#   .\start-full-to-grok2api.ps1 auth-only -AuthCount 3
#   .\start-full-to-grok2api.ps1 import-only -ImportSinceMinutes 120
#   .\start-full-to-grok2api.ps1 status
#   .\start-full-to-grok2api.ps1 stop
#
# Notes:
# - Does NOT kill main xai_enroller.service unless -StopAuthService
# - Prefer Path B browser complete (accounts.x.ai) over long empty poll loops
# - Credentials: keys/.credentials or sibling ../grok-import/.credentials

param(
  [Parameter(Position = 0)]
  [ValidateSet("all", "register-only", "auth-only", "import-only", "status", "stop", "help")]
  [string]$Action = "all",

  [int]$RegisterTarget = 0,          # 0 = use .env TARGET / unlimited
  [int]$RegisterWaitSec = 90,        # wait after starting register before auth (0=skip wait)
  [int]$AuthCount = 1,               # how many source sessions to OAuth this run
  [int]$AuthSourceIndex = -1,        # -1 = pick newest unused-ish index (tail)
  [string]$SourceFile = "",          # default auth-local/source-snapshot.jsonl
  [switch]$Headed,                   # show browser window for Path B
  [double]$BrowserTimeout = 120,
  [double]$PollTimeout = 180,
  [int]$ImportLimit = 0,             # 0 = all new
  [double]$ImportSinceMinutes = 0,   # 0 = all new vs state
  [int]$ImportBatch = 50,
  [string]$Proxy = "",
  [string]$AdminBase = "",
  [string]$Credentials = "",
  [switch]$SkipRegister,
  [switch]$SkipAuth,
  [switch]$SkipImport,
  [switch]$SkipGrok2apiCheck,
  [switch]$ForceRegister,
  [switch]$NoTokenManagerReload,
  [switch]$StopAuthService,
  [switch]$StartTokenManager
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

$Py = Join-Path $Root ".venv\Scripts\python.exe"
$Logs = Join-Path $Root "logs"
$AuthDir = Join-Path $Root "auth-local\authenticated"
$DefaultSource = Join-Path $Root "auth-local\source-snapshot.jsonl"
$KeysDir = Join-Path $Root "keys"
$CredDefault = Join-Path $KeysDir ".credentials"
$GrokImportCred = Join-Path (Split-Path $Root -Parent) "grok-import\.credentials"
$StateFile = Join-Path $KeysDir "g2a-imported-subs.txt"

New-Item -ItemType Directory -Force -Path $Logs, $KeysDir, $AuthDir, (Join-Path $Root "auth-local\claimed") | Out-Null

function Write-Info([string]$msg) { Write-Host "[*] $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg) { Write-Host "[+] $msg" -ForegroundColor Green }
function Write-Warn([string]$msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }
function Write-Err([string]$msg) { Write-Host "[x] $msg" -ForegroundColor Red }

function Get-PythonMatches([string]$pattern) {
  @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and ($_.CommandLine -match $pattern) })
}

function Get-ProxyUrl {
  if ($Proxy) { return $Proxy }
  if ($env:HTTP_PROXY) { return $env:HTTP_PROXY }
  $envFile = Join-Path $Root ".env"
  if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
      if ($line -match '^\s*HTTP_PROXY\s*=\s*(.+)\s*$') { return $Matches[1].Trim().Trim('"') }
    }
  }
  return ""
}

function Set-ProxyEnv {
  $proxyUrl = Get-ProxyUrl
  $env:HTTP_PROXY = $proxyUrl
  $env:HTTPS_PROXY = $proxyUrl
  $env:ALL_PROXY = $proxyUrl
  $env:CLOAKBROWSER_CACHE_DIR = Join-Path $Root ".cloakbrowser"
  Write-Info "proxy=$proxyUrl"
}

function Ensure-Python {
  if (-not (Test-Path $Py)) { throw "missing venv python: $Py  (create with: python -m venv .venv)" }
}

function Ensure-Credentials {
  if ($Credentials -and (Test-Path $Credentials)) { return $Credentials }
  if (Test-Path $CredDefault) { return $CredDefault }
  if (Test-Path $GrokImportCred) {
    Copy-Item $GrokImportCred $CredDefault -Force
    Write-Ok "copied admin creds -> keys\.credentials"
    return $CredDefault
  }
  Write-Warn "no keys\.credentials yet; import will try env / config.yaml bootstrapAdmin"
  return ""
}

function Test-Grok2api {
  if ($SkipGrok2apiCheck) { return $true }
  $url = if ($env:GROK2API_HEALTH) { $env:GROK2API_HEALTH } else { "http://127.0.0.1:8000/healthz" }
  try {
    $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
    if ($r.StatusCode -eq 200) {
      Write-Ok "grok2api health ok ($url)"
      return $true
    }
  } catch {
    Write-Warn "grok2api not healthy at $url : $($_.Exception.Message)"
    Write-Warn "start it first: cd ..\grok2api; bash start.sh   (or docker compose up -d)"
  }
  return $false
}

function Get-AuthJsonCount {
  if (-not (Test-Path $AuthDir)) { return 0 }
  return @(Get-ChildItem $AuthDir -Filter "xai-*.json" -File -ErrorAction SilentlyContinue).Count
}

function Get-SourceLineCount([string]$path) {
  if (-not (Test-Path $path)) { return 0 }
  return (Get-Content $path | Measure-Object -Line).Lines
}

function Resolve-SourceFile {
  if ($SourceFile) { return $SourceFile }
  if (Test-Path $DefaultSource) { return $DefaultSource }
  $sessions = Join-Path $KeysDir "auth-sessions.jsonl"
  if (Test-Path $sessions) { return $sessions }
  throw "no source session file (auth-local\source-snapshot.jsonl or keys\auth-sessions.jsonl)"
}

function Resolve-AuthIndex([string]$sourcePath, [int]$count) {
  if ($AuthSourceIndex -ge 0) { return $AuthSourceIndex }
  $n = Get-SourceLineCount $sourcePath
  if ($n -le 0) { throw "source file empty: $sourcePath" }
  # pick near the end so we prefer fresher sessions without scanning 17k lines of cookies in PS
  $start = [Math]::Max(0, $n - [Math]::Max($count, 1) - 5)
  return $start
}

function Show-Status {
  Write-Host ""
  Write-Host "=== Full chain status (register -> auth -> grok2api) ===" -ForegroundColor White
  Write-Host "root: $Root"
  $reg = Get-PythonMatches 'grok_register\.register'
  $authSvc = Get-PythonMatches 'xai_enroller\.service'
  $pathB = Get-PythonMatches 'device_flow_browser_complete'
  $importP = Get-PythonMatches 'import_authenticated_to_grok2api'
  $tm = Get-PythonMatches 'token_manager'
  Write-Host ("register          : {0}" -f $reg.Count)
  Write-Host ("pc auth service   : {0}" -f $authSvc.Count)
  Write-Host ("pathB browser auth: {0}" -f $pathB.Count)
  Write-Host ("g2a import        : {0}" -f $importP.Count)
  Write-Host ("token manager     : {0}" -f $tm.Count)
  $src = $DefaultSource
  if (Test-Path $src) {
    Write-Host ("source-snapshot   : {0} lines" -f (Get-SourceLineCount $src))
  } else { Write-Host "source-snapshot   : missing" }
  $sess = Join-Path $KeysDir "auth-sessions.jsonl"
  if (Test-Path $sess) {
    Write-Host ("auth-sessions     : {0:N0} bytes" -f (Get-Item $sess).Length)
  }
  Write-Host ("authenticated json: {0}" -f (Get-AuthJsonCount))
  if (Test-Path $StateFile) {
    Write-Host ("g2a state keys    : {0}" -f (Get-Content $StateFile | Measure-Object -Line).Lines)
  } else { Write-Host "g2a state keys    : (none yet)" }
  try {
    $h = Invoke-WebRequest -Uri "http://127.0.0.1:8000/healthz" -UseBasicParsing -TimeoutSec 3
    Write-Host ("grok2api          : up ({0})" -f $h.StatusCode)
  } catch { Write-Host "grok2api          : down" }
  foreach ($f in @("full-register.out.log","full-auth.out.log","full-import.out.log","pipeline-register.out.log")) {
    $p = Join-Path $Logs $f
    if (Test-Path $p) { Write-Host ("log {0}: {1}" -f $f, (Get-Item $p).LastWriteTime) }
  }
  Write-Host ""
}

function Start-RegisterStep {
  Ensure-Python
  Set-ProxyEnv
  $existing = Get-PythonMatches 'grok_register\.register'
  if ($existing.Count -gt 0) {
    if (-not $ForceRegister) {
      Write-Warn ("register already running PIDs={0}" -f (($existing | ForEach-Object ProcessId) -join ','))
      return
    }
    foreach ($p in $existing) {
      Write-Warn "stopping register pid=$($p.ProcessId)"
      Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
  }
  $out = Join-Path $Logs "full-register.out.log"
  $err = Join-Path $Logs "full-register.err.log"
  $pidf = Join-Path $Logs "full-register.pid"
  $args = @("-u", "-m", "grok_register.register")
  if ($RegisterTarget -gt 0) { $args += @("--target", "$RegisterTarget") }
  Write-Info "starting register target=$RegisterTarget"
  $p = Start-Process -FilePath $Py -ArgumentList $args -WorkingDirectory $Root `
    -RedirectStandardOutput $out -RedirectStandardError $err -PassThru -WindowStyle Hidden
  $p.Id | Set-Content $pidf
  Write-Ok "register started pid=$($p.Id)"
  Write-Host "  out: $out"
  if ($RegisterWaitSec -gt 0) {
    Write-Info "waiting ${RegisterWaitSec}s for sessions (not long empty OAuth poll)"
    Start-Sleep -Seconds $RegisterWaitSec
    if (Test-Path $out) { Get-Content $out -Tail 12 }
  }
}

function Invoke-AuthStep {
  Ensure-Python
  Set-ProxyEnv
  $sourcePath = Resolve-SourceFile
  $idx = Resolve-AuthIndex $sourcePath $AuthCount
  $outLog = Join-Path $Logs "full-auth.out.log"
  $errLog = Join-Path $Logs "full-auth.err.log"
  $jsonOut = Join-Path $Logs ("full-auth-result-{0}.json" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
  $script = Join-Path $Root "scripts\device_flow_browser_complete.py"
  if (-not (Test-Path $script)) { throw "missing $script" }

  $before = Get-AuthJsonCount
  Write-Info "Path B browser OAuth source=$sourcePath index=$idx count=$AuthCount headed=$Headed"
  Write-Info "before authenticated count=$before"

  $argList = @(
    "-u", $script,
    "--source-file", $sourcePath,
    "--source-index", "$idx",
    "--count", "$AuthCount",
    "--browser-timeout", "$BrowserTimeout",
    "--poll-timeout", "$PollTimeout",
    "--json-out", $jsonOut
  )
  if ($Headed) { $argList += "--headed" }

  # run in foreground; capture stdout/stderr to logs
  $proc = Start-Process -FilePath $Py -ArgumentList $argList -WorkingDirectory $Root `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog `
    -Wait -PassThru -NoNewWindow
  $code = $proc.ExitCode
  $after = Get-AuthJsonCount
  Write-Host "  auth exit=$code authenticated $before -> $after  log=$outLog"
  if (Test-Path $outLog) { Get-Content $outLog -Tail 20 }
  if ($code -ne 0 -and $after -le $before) {
    Write-Warn "auth produced no new json (exit=$code). Check source SSO freshness / proxy."
  } else {
    Write-Ok "auth step done (+$($after - $before) files)"
  }
  return $code
}

function Invoke-ImportStep {
  Ensure-Python
  $cred = Ensure-Credentials
  $script = Join-Path $Root "scripts\import_authenticated_to_grok2api.py"
  if (-not (Test-Path $script)) { throw "missing $script" }
  $outLog = Join-Path $Logs "full-import.out.log"
  $argList = @(
    "-u", $script,
    "--auth-dir", $AuthDir,
    "--state-file", $StateFile,
    "--batch", "$ImportBatch"
  )
  if ($ImportLimit -gt 0) { $argList += @("--limit", "$ImportLimit") }
  if ($ImportSinceMinutes -gt 0) { $argList += @("--since-minutes", "$ImportSinceMinutes") }
  if ($AdminBase) { $argList += @("--admin-base", $AdminBase) }
  if ($cred) { $argList += @("--credentials", $cred) }
  if ($NoTokenManagerReload) { $argList += "--no-reload" }

  Write-Info "importing authenticated json -> grok2api"
  $p = Start-Process -FilePath $Py -ArgumentList $argList -WorkingDirectory $Root `
    -RedirectStandardOutput $outLog -RedirectStandardError (Join-Path $Logs "full-import.err.log") `
    -Wait -PassThru -NoNewWindow
  if (Test-Path $outLog) {
    Get-Content $outLog -Tail 30
  }
  if ($p.ExitCode -eq 0) {
    Write-Ok "import step done (log=$outLog)"
  } else {
    Write-Err "import failed exit=$($p.ExitCode) log=$outLog"
  }
  return $p.ExitCode
}

function Start-TokenManagerIfNeeded {
  if (-not $StartTokenManager) { return }
  $existing = Get-PythonMatches 'token_manager'
  if ($existing.Count -gt 0) {
    Write-Info "token manager already running"
    return
  }
  $launcher = Join-Path $Root "start-token-manager-windows.ps1"
  if (Test-Path $launcher) {
    Write-Info "starting token manager UI :8787"
    Start-Process -FilePath "powershell" -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File",$launcher) -WindowStyle Minimized
  }
}

function Stop-Full {
  Write-Info "stop full-chain managed processes"
  foreach ($p in (Get-PythonMatches 'grok_register\.register')) {
    if ($p.CommandLine -match [regex]::Escape($Root)) {
      Write-Warn "stop register pid=$($p.ProcessId)"
      Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
  }
  foreach ($p in (Get-PythonMatches 'device_flow_browser_complete|import_authenticated_to_grok2api|full-auth-run')) {
    Write-Warn "stop auth/import pid=$($p.ProcessId)"
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
  }
  if ($StopAuthService) {
    foreach ($p in (Get-PythonMatches 'xai_enroller\.service')) {
      Write-Warn "stop xai_enroller.service pid=$($p.ProcessId)"
      Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
  } else {
    Write-Info "left xai_enroller.service running (use -StopAuthService to kill)"
  }
  Write-Ok "stop done"
}

function Invoke-All {
  Set-ProxyEnv
  Ensure-Python
  $healthy = Test-Grok2api
  if (-not $healthy) {
    Write-Warn "continuing without healthy grok2api; import may fail until service is up"
  }

  if (-not $SkipRegister -and $Action -in @("all", "register-only")) {
    if ($Action -eq "register-only" -or -not $SkipRegister) {
      Start-RegisterStep
    }
  }

  $authCode = 0
  if (-not $SkipAuth -and $Action -in @("all", "auth-only")) {
    # if source missing and we just registered, allow short extra wait for first session file growth
    try {
      $src = Resolve-SourceFile
    } catch {
      if ($RegisterWaitSec -gt 0) {
        Write-Warn "source not ready, wait extra 30s"
        Start-Sleep -Seconds 30
      }
      $src = Resolve-SourceFile
    }
    $authCode = Invoke-AuthStep
  }

  $importCode = 0
  if (-not $SkipImport -and $Action -in @("all", "import-only")) {
    $importCode = Invoke-ImportStep
  }

  Start-TokenManagerIfNeeded
  Show-Status

  if ($Action -eq "all") {
    if ($authCode -ne 0 -and $importCode -ne 0) { exit 1 }
    if ($importCode -ne 0) { exit $importCode }
  } elseif ($Action -eq "auth-only") {
    exit $authCode
  } elseif ($Action -eq "import-only") {
    exit $importCode
  }
}

switch ($Action) {
  "help" {
    Write-Host @"
start-full-to-grok2api.ps1 — one-click register -> browser OAuth -> grok2api

  all             Register (optional wait) + Path B auth + import  [default]
  register-only   Start tempmail register only
  auth-only       Browser device OAuth (accounts.x.ai) -> auth-local/authenticated
  import-only     Scan authenticated xai-*.json into grok2api
  status          Show counts / processes / health
  stop            Stop register / pathB / import (not main enroller)

Examples:
  .\start-full-to-grok2api.ps1
  .\start-full-to-grok2api.ps1 all -RegisterTarget 10 -AuthCount 2 -RegisterWaitSec 120
  .\start-full-to-grok2api.ps1 auth-only -AuthCount 3 -Headed
  .\start-full-to-grok2api.ps1 import-only -ImportSinceMinutes 60
  .\start-full-to-grok2api.ps1 import-only -ImportLimit 20
  .\start-full-to-grok2api.ps1 stop

Output dirs:
  auth-local\authenticated\xai-*.json
  keys\g2a-imported-subs.txt
  logs\full-*.log
"@
  }
  "status" { Show-Status }
  "stop" { Stop-Full }
  "register-only" {
    Set-ProxyEnv
    Ensure-Python
    Start-RegisterStep
  }
  "auth-only" {
    Set-ProxyEnv
    Ensure-Python
    $code = Invoke-AuthStep
    exit $code
  }
  "import-only" {
    Ensure-Python
    $null = Test-Grok2api
    $code = Invoke-ImportStep
    exit $code
  }
  "all" { Invoke-All }
}

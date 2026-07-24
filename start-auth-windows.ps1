# Windows auth launcher for grok-free-register
# Default: Path B browser OAuth (accounts.x.ai) — the working fix for oauth_rejected
# Optional: -LegacyService to run old xai_enroller.service interactive TUI
#
# Usage:
#   .\start-auth-windows.ps1
#   .\start-auth-windows.ps1 -AuthCount 10
#   .\start-auth-windows.ps1 -Daemon
#   .\start-auth-windows.ps1 -LegacyService
#   .\start-auth-windows.ps1 -Headed

param(
  [ValidateSet("pathb", "legacy")]
  [string]$Mode = "pathb",

  [switch]$LegacyService,          # alias for -Mode legacy
  [switch]$Daemon,                 # pathb forever (default when AuthCount=0)
  [int]$AuthCount = 0,             # 0 = run until idle loop (daemon); >0 stop after N successes
  [int]$MaxAttempts = 0,           # 0 = unlimited attempts
  [string]$SourceFile = "",
  [string]$Proxy = "",
  [switch]$Headed,
  [double]$BrowserTimeout = 120,
  [double]$PollTimeout = 180,
  [double]$IdleSleep = 20,
  [double]$FailSleep = 8,
  [int]$ScanWindow = 500,
  [switch]$FromStart,
  [switch]$Background              # detach pathb to background log
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if ($LegacyService) { $Mode = "legacy" }

function Get-ProxyUrl {
  if ($Proxy) { return $Proxy }
  if ($env:HTTP_PROXY) { return $env:HTTP_PROXY }
  $envFile = Join-Path $PSScriptRoot ".env"
  if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
      if ($line -match '^\s*HTTP_PROXY\s*=\s*(.+)\s*$') { return $Matches[1].Trim().Trim('"') }
    }
  }
  return ""
}

$proxyUrl = Get-ProxyUrl
if ($proxyUrl) {
  $env:HTTP_PROXY = $proxyUrl
  $env:HTTPS_PROXY = $proxyUrl
  $env:ALL_PROXY = $proxyUrl
}
$env:CLOAKBROWSER_CACHE_DIR = Join-Path $PSScriptRoot ".cloakbrowser"

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Missing venv python: $py" }

New-Item -ItemType Directory -Force -Path "keys", "logs", "auth-local\authenticated", "auth-local\claimed" | Out-Null
$authDir = if ($env:XAI_ENROLLER_LOCAL_AUTH_DIR) { $env:XAI_ENROLLER_LOCAL_AUTH_DIR } else { Join-Path $PSScriptRoot "auth-local" }
$env:XAI_ENROLLER_LOCAL_AUTH_DIR = $authDir
New-Item -ItemType Directory -Force -Path $authDir, (Join-Path $authDir "authenticated"), (Join-Path $authDir "claimed") | Out-Null

if (-not $SourceFile) {
  $cand = @(
    (Join-Path $PSScriptRoot "auth-local\source-snapshot.jsonl"),
    (Join-Path $PSScriptRoot "keys\auth-sessions.jsonl")
  )
  foreach ($c in $cand) { if (Test-Path $c) { $SourceFile = $c; break } }
}

Write-Host "[*] Auth launcher"
Write-Host "    mode     : $Mode"
Write-Host "    proxy    : $proxyUrl"
Write-Host "    auth dir : $authDir"
Write-Host "    source   : $(if ($SourceFile) { $SourceFile } else { '(none yet)' })"

if ($Mode -eq "legacy") {
  # Old interactive xai_enroller.service (may still hit oauth_rejected if browser confirm fails)
  $env:XAI_AUTH_SERVICE_SOURCE = if ($env:XAI_AUTH_SERVICE_SOURCE) { $env:XAI_AUTH_SERVICE_SOURCE } else { "local" }
  $env:XAI_AUTH_SERVICE_REGISTER_ROOT = if ($env:XAI_AUTH_SERVICE_REGISTER_ROOT) { $env:XAI_AUTH_SERVICE_REGISTER_ROOT } else { $PSScriptRoot }
  $env:XAI_AUTH_SERVICE_LOG_MODE = if ($env:XAI_AUTH_SERVICE_LOG_MODE) { $env:XAI_AUTH_SERVICE_LOG_MODE } else { "user" }
  $env:XAI_AUTH_SERVICE_MIN_INTERVAL_SEC = if ($env:XAI_AUTH_SERVICE_MIN_INTERVAL_SEC) { $env:XAI_AUTH_SERVICE_MIN_INTERVAL_SEC } else { "10" }
  $env:XAI_AUTH_SERVICE_RETRY_SEC = if ($env:XAI_AUTH_SERVICE_RETRY_SEC) { $env:XAI_AUTH_SERVICE_RETRY_SEC } else { "60" }
  $env:XAI_AUTH_SERVICE_SYNC_SEC = if ($env:XAI_AUTH_SERVICE_SYNC_SEC) { $env:XAI_AUTH_SERVICE_SYNC_SEC } else { "30" }
  Write-Host "    engine   : xai_enroller.service (legacy)"
  Write-Host "    commands : s status | take N | p pause | r resume | c cancel | q quit"
  Write-Host "    note     : if you see oauth_rejected, use default PathB mode instead"
  & $py -m xai_enroller.service @args
  exit $LASTEXITCODE
}

# ---- Path B (default): accounts.x.ai browser device approve closed loop ----
$daemonScript = Join-Path $PSScriptRoot "scripts\auth_pathb_daemon.py"
if (-not (Test-Path $daemonScript)) { throw "missing $daemonScript" }
if (-not $SourceFile -or -not (Test-Path $SourceFile)) {
  throw "missing source sessions. Run register first, need auth-local\source-snapshot.jsonl or keys\auth-sessions.jsonl"
}

$argList = @(
  "-u", $daemonScript,
  "--source-file", $SourceFile,
  "--state-file", (Join-Path $PSScriptRoot "keys\pathb-auth-done.txt"),
  "--browser-timeout", "$BrowserTimeout",
  "--poll-timeout", "$PollTimeout",
  "--idle-sleep", "$IdleSleep",
  "--fail-sleep", "$FailSleep",
  "--scan-window", "$ScanWindow"
)
if ($AuthCount -gt 0) { $argList += @("--count", "$AuthCount") }
if ($MaxAttempts -gt 0) { $argList += @("--max-attempts", "$MaxAttempts") }
if ($Headed) { $argList += "--headed" }
if ($FromStart) { $argList += "--from-start" }

Write-Host "    engine   : Path B device_flow_browser_complete (accounts.x.ai)"
Write-Host "    output   : $(Join-Path $authDir 'authenticated\xai-*.json')"
Write-Host "    state    : keys\pathb-auth-done.txt"
if ($AuthCount -gt 0) {
  Write-Host "    target   : $AuthCount successes then exit"
} else {
  Write-Host "    target   : continuous (Ctrl-C to stop)"
}

if ($Background) {
  $out = Join-Path $PSScriptRoot "logs\auth-pathb.out.log"
  $err = Join-Path $PSScriptRoot "logs\auth-pathb.err.log"
  $pidf = Join-Path $PSScriptRoot "logs\auth-pathb.pid"
  $p = Start-Process -FilePath $py -ArgumentList $argList -WorkingDirectory $PSScriptRoot `
    -RedirectStandardOutput $out -RedirectStandardError $err -PassThru -WindowStyle Hidden
  $p.Id | Set-Content $pidf
  Write-Host "[+] PathB auth started background pid=$($p.Id)"
  Write-Host "    out: $out"
  Write-Host "    err: $err"
  exit 0
}

& $py @argList
exit $LASTEXITCODE

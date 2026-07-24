# Pool status for register + auth inventory
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $root
$keys = Join-Path $root "keys"
$authLocal = Join-Path $root "auth-local"
$legacyAuth = Join-Path $env:USERPROFILE "Downloads\grok-free-register-auth"
if (Test-Path (Join-Path $authLocal "authenticated")) {
  $authDir = $authLocal
} elseif (Test-Path $legacyAuth) {
  $authDir = $legacyAuth
} else {
  $authDir = $authLocal
}
$accounts = Join-Path $keys "accounts.txt"
$sessions = Join-Path $keys "auth-sessions.jsonl"
$authenticated = Join-Path $authDir "authenticated"
$claimed = Join-Path $authDir "claimed"
$exportDir = Join-Path $authDir "export"

function Count-Lines([string]$path) {
  if (-not (Test-Path $path)) { return 0 }
  return @(Get-Content $path -ErrorAction SilentlyContinue | Where-Object { $_.Trim() -ne "" }).Count
}
function Count-JsonFiles([string]$path) {
  if (-not (Test-Path $path)) { return 0 }
  return @(Get-ChildItem $path -Filter *.json -File -ErrorAction SilentlyContinue).Count
}
function Count-Claimed([string]$path) {
  if (-not (Test-Path $path)) { return 0 }
  return @(Get-ChildItem $path -Recurse -Filter *.json -File -ErrorAction SilentlyContinue).Count
}
function Count-Export([string]$path) {
  if (-not (Test-Path $path)) { return 0 }
  return @(Get-ChildItem $path -Filter *.jsonl -File -ErrorAction SilentlyContinue).Count
}

$rawAccounts = Count-Lines $accounts
$rawSessions = Count-Lines $sessions
$available = Count-JsonFiles $authenticated
$claimedCount = Count-Claimed $claimed
$exportCount = Count-Export $exportDir
$registerRunning = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -match 'grok_register\.register' }).Count
$authRunning = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -match 'xai_enroller\.service' }).Count

Write-Host "=== Grok Pool Status ==="
Write-Host ("auth dir         : {0}" -f $authDir)
Write-Host ("register workers : {0}" -f $registerRunning)
Write-Host ("auth workers     : {0}" -f $authRunning)
Write-Host ("raw accounts     : {0}  ({1})" -f $rawAccounts, $accounts)
Write-Host ("raw sessions     : {0}  ({1})" -f $rawSessions, $sessions)
Write-Host ("available keys   : {0}  ({1})" -f $available, $authenticated)
Write-Host ("claimed keys     : {0}  ({1})" -f $claimedCount, $claimed)
Write-Host ("export files     : {0}  ({1})" -f $exportCount, $exportDir)
if (($available + $claimedCount) -gt 0) {
  $ratio = [math]::Round(100.0 * $available / ($available + $claimedCount), 1)
  Write-Host ("available ratio  : {0}%" -f $ratio)
}
Write-Host ""
Write-Host "Average consumption tips:"
Write-Host "  - keep register filling raw sessions continuously"
Write-Host "  - auth converts with min interval 10s, retry 60s"
Write-Host "  - claim with: take N   (only from available)"
Write-Host "  - export with: ops/export-unified-keys.ps1"
Write-Host "  - push Azure: .tools/azure-vps/push-keys-to-azure.ps1 -Source export"
Write-Host "  - target available buffer = daily_claim * 2"

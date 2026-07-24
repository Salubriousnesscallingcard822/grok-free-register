# Adaptive high-concurrency register launcher with rollback window.
param(
  [ValidateSet("safe","balanced","boost","turbo","auto")]
  [string]$Mode = "auto",
  [switch]$NoApply,
  [switch]$DryRun
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$env:HTTP_PROXY = if ($env:HTTP_PROXY) { $env:HTTP_PROXY } else { "" }
$env:HTTPS_PROXY = if ($env:HTTPS_PROXY) { $env:HTTPS_PROXY } else { "" }
$env:ALL_PROXY = if ($env:ALL_PROXY) { $env:ALL_PROXY } else { $env:HTTPS_PROXY }
$env:CLOAKBROWSER_CACHE_DIR = Join-Path $PSScriptRoot ".cloakbrowser"

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Missing venv python: $py" }
New-Item -ItemType Directory -Force -Path "keys","logs","ops\capacity-backups" | Out-Null

# single-instance guard
$existing = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -match 'grok_register\.register' })
if ($existing.Count -gt 0) {
  throw "Register already running: PIDs $(($existing | ForEach-Object ProcessId) -join ',')"
}

Write-Host "[*] Adaptive Grok Register"
Write-Host "    Mode request : $Mode"
Write-Host "    Proxy        : $env:HTTP_PROXY"

if (-not $NoApply) {
  if ($Mode -eq "auto") {
    & $py scripts\capacity_control.py apply | Tee-Object -FilePath "logs\capacity-apply.out.log"
  } else {
    & $py scripts\capacity_control.py apply --mode $Mode | Tee-Object -FilePath "logs\capacity-apply.out.log"
  }
  if ($LASTEXITCODE -ne 0) { throw "capacity apply failed" }
} else {
  Write-Host "    Skip apply   : using current .env"
}

& $py scripts\capacity_control.py status | Tee-Object -FilePath "logs\capacity-status.out.log"
Write-Host ""
Write-Host "[*] Rollback window"
Write-Host "    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\rollback-capacity.ps1"
Write-Host "    or: python scripts\capacity_control.py rollback"
Write-Host ""

if ($DryRun) {
  Write-Host "[*] DryRun only, not starting register."
  exit 0
}

Write-Host "[*] Starting register with adaptive capacity..."
& $py -m cloakbrowser info --quick
& $py -m grok_register.register @args

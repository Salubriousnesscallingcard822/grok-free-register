# Windows launcher for grok-free-register
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if ($env:HTTP_PROXY) { $env:HTTP_PROXY = $env:HTTP_PROXY }
if ($env:HTTPS_PROXY) { $env:HTTPS_PROXY = $env:HTTPS_PROXY } elseif ($env:HTTP_PROXY) { $env:HTTPS_PROXY = $env:HTTP_PROXY }
$env:CLOAKBROWSER_CACHE_DIR = Join-Path $PSScriptRoot ".cloakbrowser"

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Set-Content -Path ".env" -Value "EMAIL_MODE=tempmail`nTEMPMAIL_PROVIDER_ORDER=lol,mailtm`n# HTTP_PROXY=http://127.0.0.1:YOUR_PROXY_PORT`n# HTTPS_PROXY=http://127.0.0.1:YOUR_PROXY_PORT`nTARGET=0`nREGISTER_LOG_MODE=user`nPHYSICAL_CAP=6`nT_SLOT_CAP=16`nQ_SLOT_CAP=16`nQ_PENDING_CAP=24`nP_BATCH_MAX=6" -Encoding ASCII
}

New-Item -ItemType Directory -Force -Path "keys", "logs" | Out-Null

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Missing venv python: $py" }

Write-Host "[*] CloakBrowser info"
& $py -m cloakbrowser info --quick

Write-Host "[*] Starting register (Ctrl-C to stop)"
& $py -m grok_register.register @args


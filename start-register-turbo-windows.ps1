# High-throughput Windows register launcher
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$env:HTTP_PROXY = if ($env:HTTP_PROXY) { $env:HTTP_PROXY } else { "" }
$env:HTTPS_PROXY = if ($env:HTTPS_PROXY) { $env:HTTPS_PROXY } else { "" }
$env:CLOAKBROWSER_CACHE_DIR = Join-Path $PSScriptRoot ".cloakbrowser"

# ensure turbo defaults exist / refresh non-destructively only if missing keys
if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env" -ErrorAction SilentlyContinue
}

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Missing venv python: $py" }

New-Item -ItemType Directory -Force -Path "keys", "logs" | Out-Null

Write-Host "[*] Grok register TURBO mode"
Write-Host "    PHYSICAL_CAP from .env (recommended 6-10 on 12c/16G)"
Write-Host "    TARGET=0 means unlimited until Ctrl-C"
Write-Host "    Do NOT run multiple register processes at once"

Write-Host "    Tip: for adaptive concurrency + rollback window use"
Write-Host "         .\start-register-adaptive-windows.ps1 -Mode auto"
Write-Host "         .\start-register-adaptive-windows.ps1 -Mode turbo"
Write-Host "         .\scripts\rollback-capacity.ps1"


# single-instance guard
$existing = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -match 'grok_register\.register' })
if ($existing.Count -gt 0) {
  throw "Register already running: PIDs $(($existing | ForEach-Object ProcessId) -join ',')"
}

& $py -m cloakbrowser info --quick
& $py -m grok_register.register @args


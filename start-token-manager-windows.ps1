# Grok Tool launcher (KeyHub-style shell over unified token pool)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$env:HTTP_PROXY = if ($env:HTTP_PROXY) { $env:HTTP_PROXY } else { "http://127.0.0.1:7897" }
$env:HTTPS_PROXY = if ($env:HTTPS_PROXY) { $env:HTTPS_PROXY } else { "http://127.0.0.1:7897" }
$env:TOKEN_MANAGER_HOST = if ($env:TOKEN_MANAGER_HOST) { $env:TOKEN_MANAGER_HOST } else { "127.0.0.1" }
$env:TOKEN_MANAGER_PORT = if ($env:TOKEN_MANAGER_PORT) { $env:TOKEN_MANAGER_PORT } else { "8787" }
$env:TOKEN_MANAGER_FREE_UNITS = if ($env:TOKEN_MANAGER_FREE_UNITS) { $env:TOKEN_MANAGER_FREE_UNITS } else { "100" }

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Missing venv python: $py" }

$authLocal = Join-Path $PSScriptRoot "auth-local"
$tokensDir = Join-Path $authLocal "authenticated"
$dataDir = Join-Path $authLocal "token-manager"
New-Item -ItemType Directory -Force -Path $tokensDir, $dataDir, (Join-Path $PSScriptRoot "logs") | Out-Null

Write-Host "[*] Grok Tool"
Write-Host "    UI       : http://$($env:TOKEN_MANAGER_HOST):$($env:TOKEN_MANAGER_PORT)/"
Write-Host "    Base URL : http://$($env:TOKEN_MANAGER_HOST):$($env:TOKEN_MANAGER_PORT)/v1"
Write-Host "    Tokens   : $tokensDir"
Write-Host "    Data     : $dataDir"
Write-Host "    Shell    : KeyHub-style local manager"

$openScript = @(
  'Start-Sleep -Seconds 1',
  'try { Start-Process "http://127.0.0.1:8787/" } catch {}'
) -join "`r`n"
$openPath = Join-Path $PSScriptRoot "logs\open-grok-tool.ps1"
Set-Content -Path $openPath -Value $openScript -Encoding UTF8
Start-Process -FilePath powershell -ArgumentList "-NoProfile","-ExecutionPolicy","Bypass","-File",$openPath -WindowStyle Hidden | Out-Null

& $py -m token_manager @args

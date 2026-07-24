# Grok Tool launcher (KeyHub-style shell over unified token pool)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$env:HTTP_PROXY = if ($env:HTTP_PROXY) { $env:HTTP_PROXY } else { "" }
$env:HTTPS_PROXY = if ($env:HTTPS_PROXY) { $env:HTTPS_PROXY } else { "" }
$env:TOKEN_MANAGER_HOST = if ($env:TOKEN_MANAGER_HOST) { $env:TOKEN_MANAGER_HOST } else { "127.0.0.1" }
$env:TOKEN_MANAGER_PORT = if ($env:TOKEN_MANAGER_PORT) { $env:TOKEN_MANAGER_PORT } else { "8787" }
$env:TOKEN_MANAGER_FREE_UNITS = if ($env:TOKEN_MANAGER_FREE_UNITS) { $env:TOKEN_MANAGER_FREE_UNITS } else { "100" }
$env:GROK_TOOL_DESKTOP = if ($env:GROK_TOOL_DESKTOP) { $env:GROK_TOOL_DESKTOP } else { "1" }
$env:GROK_TOOL_OPEN_BROWSER = if ($env:GROK_TOOL_OPEN_BROWSER) { $env:GROK_TOOL_OPEN_BROWSER } else { "0" }
$env:XAI_AUTH_SERVICE_SOURCE = if ($env:XAI_AUTH_SERVICE_SOURCE) { $env:XAI_AUTH_SERVICE_SOURCE } else { "local" }
$env:XAI_AUTH_SERVICE_REGISTER_ROOT = if ($env:XAI_AUTH_SERVICE_REGISTER_ROOT) { $env:XAI_AUTH_SERVICE_REGISTER_ROOT } else { $PSScriptRoot }
$env:XAI_ENROLLER_LOCAL_AUTH_DIR = if ($env:XAI_ENROLLER_LOCAL_AUTH_DIR) { $env:XAI_ENROLLER_LOCAL_AUTH_DIR } else { Join-Path $PSScriptRoot "auth-local" }

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Missing venv python: $py" }

$authLocal = Join-Path $PSScriptRoot "auth-local"
$tokensDir = Join-Path $authLocal "authenticated"
$dataDir = Join-Path $authLocal "token-manager"
New-Item -ItemType Directory -Force -Path $tokensDir, $dataDir, (Join-Path $PSScriptRoot "logs"), (Join-Path $PSScriptRoot "keys") | Out-Null

Write-Host "[*] Grok Tool Desktop"
Write-Host "    UI       : http://$($env:TOKEN_MANAGER_HOST):$($env:TOKEN_MANAGER_PORT)/"
Write-Host "    Base URL : http://$($env:TOKEN_MANAGER_HOST):$($env:TOKEN_MANAGER_PORT)/v1"
Write-Host "    Tokens   : $tokensDir"
Write-Host "    Data     : $dataDir"
Write-Host "    Pipeline : register + auth convert controls enabled"
Write-Host "    Shell    : native desktop window (pywebview) with browser fallback"

& $py -m token_manager @args

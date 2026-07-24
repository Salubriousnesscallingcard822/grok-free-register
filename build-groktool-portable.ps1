# Build portable GrokTool.exe
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "missing venv: $py" }

$env:PYINSTALLER_CONFIG_DIR = Join-Path $PSScriptRoot "build\pyinstaller-config"
$env:TEMP = Join-Path $PSScriptRoot "build\tmp"
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:PYINSTALLER_CONFIG_DIR, $env:TEMP | Out-Null

$portable = Join-Path $PSScriptRoot "dist\GrokTool-Portable"
if (Test-Path $portable) {
  $privateFiles = Get-ChildItem `
    -File `
    -Recurse `
    -ErrorAction SilentlyContinue `
    (Join-Path $portable "tokens"), (Join-Path $portable "data")
  if ($privateFiles) {
    throw "portable output contains credentials/state; move it before rebuilding: $portable"
  }
}

& $py -c "import PyInstaller, httpx"
if ($LASTEXITCODE -ne 0) {
  Write-Host "[*] install pyinstaller"
  & $py -m pip install pyinstaller==6.14.2 httpx --disable-pip-version-check
  if ($LASTEXITCODE -ne 0) { throw "pip failed" }
}

Write-Host "[*] build onefile exe"
& $py -m PyInstaller --noconfirm --clean --distpath (Join-Path $PSScriptRoot "dist") --workpath (Join-Path $PSScriptRoot "build\pyinstaller") (Join-Path $PSScriptRoot "GrokTool.spec")
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }

New-Item -ItemType Directory -Force -Path $portable, (Join-Path $portable "tokens"), (Join-Path $portable "data") | Out-Null
Copy-Item (Join-Path $PSScriptRoot "dist\GrokTool.exe") (Join-Path $portable "GrokTool.exe") -Force

$bat = @"
@echo off
cd /d "%~dp0"
set GROK_TOOL_PORTABLE=1
set GROK_TOOL_OPEN_BROWSER=1
set TOKEN_MANAGER_HOST=127.0.0.1
set TOKEN_MANAGER_PORT=8787
if not defined HTTP_PROXY set HTTP_PROXY=
if not defined HTTPS_PROXY set HTTPS_PROXY=
echo [*] Grok Tool Portable
echo     UI      : http://127.0.0.1:8787/
echo     BaseURL : http://127.0.0.1:8787/v1
echo     tokens  : %~dp0tokens
echo     data    : %~dp0data
echo     Drop OAuth json files into tokens\ then click refresh
GrokTool.exe %*
"@
Set-Content -Path (Join-Path $portable "Start-GrokTool.bat") -Value $bat -Encoding ASCII

$readme = @"
Grok Tool Portable
==================

Double click:
  Start-GrokTool.bat
  or GrokTool.exe

Web UI:
  http://127.0.0.1:8787/

OpenAI compatible:
  Base URL = http://127.0.0.1:8787/v1
  API Key  = Master Key shown in the local UI

Windows key storage:
  data\master-key.dpapi is encrypted for the current Windows user/machine.
  Moving the data folder to another user/machine will not preserve the key.

How to fill pool:
  1. Drop OAuth json into tokens\ (xai-xxxxxxxx.json with access_token/refresh_token)
  2. Open web UI and click reload pool
  3. Overview shows usable accounts / remaining units

Folders:
  GrokTool.exe
  Start-GrokTool.bat
  tokens\     <- drop token json here
  data\       <- encrypted master key + signed state

Proxy:
  Default 
  Direct: set TOKEN_MANAGER_PROXY=
"@
Set-Content -Path (Join-Path $portable "README.txt") -Value $readme -Encoding UTF8

$zipPath = Join-Path $PSScriptRoot "dist\GrokTool-Portable.zip"
Compress-Archive `
  -Path $portable `
  -DestinationPath $zipPath `
  -CompressionLevel Optimal `
  -Force

Write-Host "[+] portable package: $portable"
Write-Host "[+] portable archive: $zipPath"
Get-ChildItem $portable | Format-Table Name, Length

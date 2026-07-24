# Thin PowerShell wrapper for start_all_windows.py
param(
  [Parameter(Position = 0)]
  [string]$Action = "status"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "missing $py" }
& $py (Join-Path $PSScriptRoot "start_all_windows.py") @args

# Show current / recommended concurrency mode.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..
$py = Join-Path $PWD ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
& $py scripts\capacity_control.py status

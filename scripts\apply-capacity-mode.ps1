# Apply a capacity mode into .env with automatic backup (rollback window).
param(
  [ValidateSet("safe","balanced","boost","turbo","auto")]
  [string]$Mode = "auto"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..
$py = Join-Path $PWD ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
if ($Mode -eq "auto") {
  & $py scripts\capacity_control.py apply
} else {
  & $py scripts\capacity_control.py apply --mode $Mode
}

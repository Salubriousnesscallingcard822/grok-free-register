# Rollback .env to previous capacity snapshot.
param(
  [string]$Backup = ""
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..
$py = Join-Path $PWD ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
if ($Backup) {
  & $py scripts\capacity_control.py rollback --backup $Backup
} else {
  & $py scripts\capacity_control.py rollback
}

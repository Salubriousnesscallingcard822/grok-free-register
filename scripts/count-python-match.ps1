param([Parameter(Mandatory=$true)][string]$Pattern)
$n = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and ($_.CommandLine -match $Pattern) }).Count
Write-Output $n
# Local custom-domain email receiver for EMAIL_MODE=custom
# Domain: yanqiudesu.top
param(
  [int]$Port = 8088,
  [string]$Domain = "yanqiudesu.top",
  [switch]$Foreground
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\\Scripts\\python.exe"
if (-not (Test-Path $py)) { throw "missing $py" }
$tokenFile = Join-Path $PSScriptRoot "keys\\email-webhook-token.txt"
if (-not (Test-Path $tokenFile)) {
  $t = -join ((48..57+97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
  New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot "keys") | Out-Null
  Set-Content $tokenFile $t -Encoding ASCII -NoNewline
}
$token = (Get-Content $tokenFile -Raw).Trim()
$env:EMAIL_DOMAIN = $Domain
$env:EMAIL_PORT = "$Port"
$env:WEBHOOK_TOKEN = $token
$env:EMAIL_WEBHOOK_TOKEN = $token
New-Item -ItemType Directory -Force -Path "logs" | Out-Null
Write-Host "[*] custom email server"
Write-Host "    domain : $Domain"
Write-Host "    local  : http://127.0.0.1:$Port/webhook"
Write-Host "    health : http://127.0.0.1:$Port/health"
Write-Host "    token  : keys\\email-webhook-token.txt"
if ($Foreground) {
  & $py -u -m grok_register.email_server --domain $Domain --port $Port
  exit $LASTEXITCODE
}
$out = Join-Path $PSScriptRoot "logs\\email-server.out.log"
$err = Join-Path $PSScriptRoot "logs\\email-server.err.log"
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -match 'grok_register\\.email_server' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1
$p = Start-Process -FilePath $py -ArgumentList @("-u","-m","grok_register.email_server","--domain",$Domain,"--port","$Port") -WorkingDirectory $PSScriptRoot -RedirectStandardOutput $out -RedirectStandardError $err -PassThru -WindowStyle Hidden
$p.Id | Set-Content (Join-Path $PSScriptRoot "logs\\email-server.pid")
Write-Host "[+] started pid=$($p.Id)"
Start-Sleep -Seconds 1
try { Write-Host ((Invoke-WebRequest "http://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 3).Content) } catch { Write-Host "[!] health not ready yet" }

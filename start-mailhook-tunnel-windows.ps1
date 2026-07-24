# Expose local email webhook via cloudflared
# Preferred hostname: mailhook.example.com
#
# Named:
#   cloudflared tunnel login
#   cloudflared tunnel create yanqiu-mailhook
#   cloudflared tunnel route dns yanqiu-mailhook mailhook.example.com
#   .\start-mailhook-tunnel-windows.ps1 -Named
# Quick:
#   .\start-mailhook-tunnel-windows.ps1 -Quick

param(
  [switch]$Named,
  [switch]$Quick,
  [string]$Hostname = "mailhook.example.com",
  [string]$TunnelName = "yanqiu-mailhook",
  [int]$LocalPort = 8088
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force -Path "logs","cloudflare" | Out-Null
if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) { throw "cloudflared not found" }
if (-not $Named -and -not $Quick) { $Named = $true }

if ($Quick) {
  Write-Host "[*] QUICK tunnel -> http://127.0.0.1:$LocalPort"
  Write-Host "    copy https://*.trycloudflare.com into Worker WEBHOOK_URL + /webhook"
  $out = Join-Path $PSScriptRoot "logs\mailhook-tunnel.out.log"
  $err = Join-Path $PSScriptRoot "logs\mailhook-tunnel.err.log"
  $p = Start-Process -FilePath "cloudflared" -ArgumentList @("tunnel","--url","http://127.0.0.1:$LocalPort","--no-autoupdate") -WorkingDirectory $PSScriptRoot -RedirectStandardOutput $out -RedirectStandardError $err -PassThru -WindowStyle Hidden
  $p.Id | Set-Content (Join-Path $PSScriptRoot "logs\mailhook-tunnel.pid")
  Write-Host "[+] cloudflared pid=$($p.Id)"
  Start-Sleep -Seconds 4
  if (Test-Path $err) { Get-Content $err -Tail 30 }
  if (Test-Path $out) { Get-Content $out -Tail 30 }
  exit 0
}

$cfgDir = Join-Path $env:USERPROFILE ".cloudflared"
$cfg = Join-Path $PSScriptRoot "cloudflare\mailhook-tunnel.yml"
Write-Host "[*] named tunnel host=$Hostname name=$TunnelName"
if (-not (Test-Path $cfg)) {
  $tunnelId = ""
  if (Test-Path $cfgDir) {
    $cred = Get-ChildItem $cfgDir -Filter "*.json" -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne "cert.pem" } | Select-Object -First 1
    if ($cred) { $tunnelId = [IO.Path]::GetFileNameWithoutExtension($cred.Name) }
  }
  $credPath = if ($tunnelId) { Join-Path $cfgDir "$tunnelId.json" } else { Join-Path $cfgDir "TUNNEL_ID.json" }
  @(
    "tunnel: $tunnelId",
    "credentials-file: $credPath",
    "ingress:",
    "  - hostname: $Hostname",
    "    service: http://127.0.0.1:$LocalPort",
    "  - service: http_status:404"
  ) | Set-Content $cfg -Encoding UTF8
  Write-Host "[!] wrote template $cfg (edit tunnel id if empty)"
}
$out = Join-Path $PSScriptRoot "logs\mailhook-tunnel.out.log"
$err = Join-Path $PSScriptRoot "logs\mailhook-tunnel.err.log"
$p = Start-Process -FilePath "cloudflared" -ArgumentList @("tunnel","--config",$cfg,"run") -WorkingDirectory $PSScriptRoot -RedirectStandardOutput $out -RedirectStandardError $err -PassThru -WindowStyle Hidden
$p.Id | Set-Content (Join-Path $PSScriptRoot "logs\mailhook-tunnel.pid")
Write-Host "[+] cloudflared pid=$($p.Id)"
Start-Sleep -Seconds 2
if (Test-Path $err) { Get-Content $err -Tail 20 }
if (Test-Path $out) { Get-Content $out -Tail 20 }

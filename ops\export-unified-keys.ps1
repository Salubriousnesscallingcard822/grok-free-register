# Export unified keys from available or a claimed batch
param(
  [ValidateSet("available","claimed-latest","claimed")]
  [string]$Source = "available",
  [string]$BatchId = "",
  [string]$OutFile = "",
  [string]$AuthDir = ""
)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $AuthDir) {
  $candidate = Join-Path $ProjectRoot "auth-local"
  $legacy = Join-Path $env:USERPROFILE "Downloads\grok-free-register-auth"
  if (Test-Path (Join-Path $candidate "authenticated")) {
    $AuthDir = $candidate
  } elseif (Test-Path $legacy) {
    $AuthDir = $legacy
  } else {
    $AuthDir = $candidate
  }
}
$authenticated = Join-Path $AuthDir "authenticated"
$claimed = Join-Path $AuthDir "claimed"
$exportDir = Join-Path $AuthDir "export"
New-Item -ItemType Directory -Force -Path $exportDir | Out-Null

$files = @()
if ($Source -eq "available") {
  $files = @(Get-ChildItem $authenticated -Filter *.json -File -ErrorAction SilentlyContinue)
} elseif ($Source -eq "claimed-latest") {
  $latest = Get-ChildItem $claimed -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
  if (-not $latest) { throw "No claimed batch found" }
  $files = @(Get-ChildItem $latest.FullName -Filter *.json -File)
  Write-Host "Using batch: $($latest.Name)"
} else {
  if (-not $BatchId) { throw "BatchId is required for Source=claimed" }
  $dir = Join-Path $claimed $BatchId
  if (-not (Test-Path $dir)) { throw "Batch not found: $dir" }
  $files = @(Get-ChildItem $dir -Filter *.json -File)
}

if (-not $files -or @($files).Count -eq 0) {
  throw "No credential json files found under $authenticated (Source=$Source)"
}

if (-not $OutFile) {
  $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $OutFile = Join-Path $exportDir "unified-keys-$Source-$stamp.jsonl"
}

$lines = foreach ($f in $files) {
  $doc = Get-Content $f.FullName -Raw | ConvertFrom-Json
  [pscustomobject]@{
    type = $doc.type
    auth_kind = $doc.auth_kind
    base_url = $doc.base_url
    access_token = $doc.access_token
    refresh_token = $doc.refresh_token
    id_token = $doc.id_token
    expires_in = $doc.expires_in
    expired = $doc.expired
    sub = $doc.sub
    token_endpoint = $doc.token_endpoint
    source_file = $f.Name
  } | ConvertTo-Json -Compress
}
[System.IO.File]::WriteAllLines($OutFile, @($lines), [System.Text.UTF8Encoding]::new($false))
Write-Host ("Exported {0} unified keys -> {1}" -f @($lines).Count, $OutFile)
Write-Host ("AuthDir: {0}" -f $AuthDir)

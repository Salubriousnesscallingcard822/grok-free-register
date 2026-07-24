param(
    [string]$ExePath = "dist\GrokTool-Portable\GrokTool.exe",
    [switch]$KeepRunning
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$resolvedExe = (Resolve-Path $ExePath).Path
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$smokeDir = Join-Path $PWD "build\smoke-$stamp"
$tokensDir = Join-Path $smokeDir "tokens"
$dataDir = Join-Path $smokeDir "data"
New-Item -ItemType Directory -Force -Path $tokensDir, $dataDir | Out-Null
Copy-Item $resolvedExe (Join-Path $smokeDir "GrokTool.exe") -Force

$fakeCredential = @{
    sub = "smoke-account"
    email = "smoke@example.invalid"
    access_token = "smoke-access-token-not-real"
    refresh_token = "smoke-refresh-token-not-real"
} | ConvertTo-Json
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText(
    (Join-Path $tokensDir "smoke.json"),
    $fakeCredential,
    $utf8NoBom
)

$listener = [System.Net.Sockets.TcpListener]::new(
    [System.Net.IPAddress]::Loopback,
    0
)
$listener.Start()
$port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
$listener.Stop()

$env:TOKEN_MANAGER_PORT = [string]$port
$env:TOKEN_MANAGER_HOST = "127.0.0.1"
$env:TOKEN_MANAGER_PROXY = ""
$env:GROK_TOOL_OPEN_BROWSER = "0"
$process = Start-Process `
    -FilePath (Join-Path $smokeDir "GrokTool.exe") `
    -WorkingDirectory $smokeDir `
    -PassThru `
    -WindowStyle Hidden

try {
    $bootstrap = $null
    for ($attempt = 0; $attempt -lt 80; $attempt++) {
        Start-Sleep -Milliseconds 250
        try {
            $bootstrap = Invoke-RestMethod `
                -Uri "http://127.0.0.1:$port/api/bootstrap" `
                -TimeoutSec 2
            break
        } catch {
        }
    }
    if (-not $bootstrap) {
        throw "portable EXE did not become ready"
    }
    $duplicateProcess = Start-Process `
        -FilePath (Join-Path $smokeDir "GrokTool.exe") `
        -WorkingDirectory $smokeDir `
        -PassThru `
        -Wait `
        -WindowStyle Hidden
    if ($duplicateProcess.ExitCode -ne 2) {
        throw "duplicate instance exit code was $($duplicateProcess.ExitCode), expected 2"
    }
    if ($bootstrap.balance.accounts_total -ne 1) {
        throw "unexpected account total: $($bootstrap.balance.accounts_total)"
    }
    if ($bootstrap.balance.accounts_usable_now -ne 1) {
        throw "unexpected usable total: $($bootstrap.balance.accounts_usable_now)"
    }
    if ($bootstrap.balance.free_units_remaining -ne 100) {
        throw "unexpected remaining units: $($bootstrap.balance.free_units_remaining)"
    }

    $sameOrigin = Invoke-WebRequest `
        -UseBasicParsing `
        -Uri "http://127.0.0.1:$port/api/bootstrap" `
        -Headers @{ Origin = "http://127.0.0.1:$port" } `
        -TimeoutSec 3
    if ($sameOrigin.StatusCode -ne 200) {
        throw "same-origin request failed"
    }

    $crossOriginStatus = 0
    try {
        Invoke-WebRequest `
            -UseBasicParsing `
            -Uri "http://127.0.0.1:$port/api/bootstrap" `
            -Headers @{ Origin = "https://example.com" } `
            -TimeoutSec 3 | Out-Null
    } catch {
        $crossOriginStatus = [int]$_.Exception.Response.StatusCode
    }
    if ($crossOriginStatus -notin @(401, 403)) {
        throw "unexpected cross-origin status: $crossOriginStatus"
    }

    $page = Invoke-WebRequest `
        -UseBasicParsing `
        -Uri "http://127.0.0.1:$port/" `
        -TimeoutSec 3
    $script = Invoke-WebRequest `
        -UseBasicParsing `
        -Uri "http://127.0.0.1:$port/static/app.js" `
        -TimeoutSec 3
    if ($page.Content -notmatch "KeyHub") {
        throw "KeyHub dashboard surface missing"
    }
    if ($script.Content -notmatch "AbortController") {
        throw "frontend timeout code missing"
    }
    if ($script.Content -notmatch "accounts_usable_now") {
        throw "frontend balance contract missing"
    }

    $stateText = Get-Content `
        -Raw `
        -Encoding UTF8 `
        (Join-Path $dataDir "pool-state.json")
    if ($stateText -match "smoke-access-token-not-real|smoke-refresh-token-not-real") {
        throw "state file leaked OAuth secrets"
    }
    $state = $stateText | ConvertFrom-Json
    if (-not $state.integrity) {
        throw "state integrity signature missing"
    }
    $encryptedMasterPath = Join-Path $dataDir "master-key.dpapi"
    if (-not (Test-Path $encryptedMasterPath)) {
        throw "DPAPI master-key file missing"
    }
    if (Test-Path (Join-Path $dataDir "master-key.txt")) {
        throw "plaintext master-key file still exists"
    }
    $encryptedMaster = [System.IO.File]::ReadAllBytes($encryptedMasterPath)
    $encryptedAsText = [System.Text.Encoding]::UTF8.GetString($encryptedMaster)
    if ($encryptedAsText.Contains([string]$bootstrap.master_key)) {
        throw "DPAPI file contains plaintext master key"
    }

    [pscustomobject]@{
        SmokeDir = $smokeDir
        ProcessId = $process.Id
        Port = $port
        BootstrapStatus = 200
        SameOriginStatus = $sameOrigin.StatusCode
        CrossOriginStatus = $crossOriginStatus
        DuplicateInstanceExitCode = $duplicateProcess.ExitCode
        AccountsTotal = $bootstrap.balance.accounts_total
        AccountsUsable = $bootstrap.balance.accounts_usable_now
        RemainingUnits = $bootstrap.balance.free_units_remaining
        StateSecretsRedacted = $true
        StateIntegrity = $true
        MasterKeyEncrypted = $true
        ExeSha256 = (Get-FileHash (Join-Path $smokeDir "GrokTool.exe") -Algorithm SHA256).Hash
        DataAcl = (icacls $dataDir | Out-String).Trim()
    }
} finally {
    if (-not $KeepRunning) {
        $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $($process.Id)" `
            -ErrorAction SilentlyContinue
        foreach ($child in $children) {
            Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    }
}

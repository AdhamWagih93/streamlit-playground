Param(
    [int]$Port = 8010
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot ".."))
$dataDir = Join-Path $repoRoot "data"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

$logOut = Join-Path $dataDir "scheduler.out.log"
$logErr = Join-Path $dataDir "scheduler.err.log"

$env:SCHEDULER_MCP_TRANSPORT = "http"
$env:SCHEDULER_MCP_HOST = "127.0.0.1"
$env:SCHEDULER_MCP_PORT = "$Port"
$env:SCHEDULER_TICK_SECONDS = "5"
$env:SCHEDULER_BOOTSTRAP_JOBS = "true"

Write-Host "Starting scheduler MCP server on http://127.0.0.1:$Port"
Write-Host "Logs: $logOut / $logErr"

$p = Start-Process -FilePath "python" \
    -ArgumentList @("-m", "src.scheduler.main") \
    -WorkingDirectory $repoRoot \
    -WindowStyle Hidden \
    -RedirectStandardOutput $logOut \
    -RedirectStandardError $logErr \
    -PassThru

Write-Host "Started PID: $($p.Id)"

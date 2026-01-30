# Best Streamlit Website - Development Deploy Script (Windows/PowerShell)
# Applies changes with minimal restarts: only services with changed images/config are recreated.
#
# How it works:
# - Captures container IDs before
# - Runs `docker compose up -d` (optionally `--pull` / `--build`)
# - Captures container IDs after and prints which ones changed

param(
    [switch]$WithAI,      # Include Ollama service
    [switch]$WithTools,   # Include development tools (DB admin)
    [switch]$Full,        # Include all optional services
    [switch]$Build,       # Rebuild local images (equivalent to compose --build)
    [switch]$Pull         # Pull newer images where applicable (compose --pull always)
)

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Best Streamlit Website - Dev Deploy" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""

# Work around Docker Desktop BuildKit/Buildx issues on some Windows setups
$env:DOCKER_BUILDKIT = "0"
$env:COMPOSE_DOCKER_CLI_BUILD = "0"

# Check if Docker is running
try {
    docker info | Out-Null
} catch {
    Write-Host "ERROR: Docker is not running. Please start Docker Desktop." -ForegroundColor Red
    exit 1
}

# Navigate to repository root
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptPath
Set-Location $repoRoot

# Build compose command (Docker Compose v2)
$composeCmd = "docker compose -f docker-compose.yml -f docker-compose.dev.yml"
$env:COMPOSE_PROJECT_NAME = "bsw"

# Compute profiles and set COMPOSE_PROFILES env var
$profiles = @()
if ($WithAI -or $Full) { $profiles += "ai" }
if ($WithTools -or $Full) { $profiles += "tools" }
if ($Full) { $profiles += "full" }

if ($profiles.Count -gt 0) {
    $env:COMPOSE_PROFILES = ($profiles -join ",")
} else {
    $env:COMPOSE_PROFILES = $null
}

# Get service list from the effective compose config
$services = @(Invoke-Expression "$composeCmd config --services" | Where-Object { $_ -and $_.Trim() -ne "" })
if (-not $services -or $services.Count -eq 0) {
    Write-Host "ERROR: No services found in compose config." -ForegroundColor Red
    exit 1
}

function Get-ServiceContainerId([string]$svc) {
    try {
        $id = (Invoke-Expression "$composeCmd ps -q $svc" | Select-Object -First 1)
        if ($id) { return $id.Trim() }
    } catch {
        return ""
    }
    return ""
}

$before = @{}
foreach ($s in $services) {
    $before[$s] = (Get-ServiceContainerId $s)
}

$cmdArgs = @("up", "-d", "--remove-orphans")
if ($Pull) { $cmdArgs += @("--pull", "always") }
if ($Build) { $cmdArgs += "--build" }

Write-Host "Applying changes (minimal restarts)..." -ForegroundColor Green
Write-Host "Command: $composeCmd $($cmdArgs -join ' ')" -ForegroundColor Gray
Write-Host ""

Invoke-Expression "$composeCmd $($cmdArgs -join ' ')"
if ($LASTEXITCODE -ne 0) {
    Write-Host "" 
    Write-Host "ERROR: Deploy failed." -ForegroundColor Red
    exit 1
}

$after = @{}
foreach ($s in $services) {
    $after[$s] = (Get-ServiceContainerId $s)
}

$changed = @()
$started = @()
$stopped = @()
foreach ($s in $services) {
    $b = $before[$s]
    $a = $after[$s]
    if (-not $b -and $a) {
        $started += $s
    } elseif ($b -and -not $a) {
        $stopped += $s
    } elseif ($b -and $a -and $b -ne $a) {
        $changed += $s
    }
}

Write-Host "" 
Write-Host "=====================================" -ForegroundColor Green
Write-Host "Deploy complete" -ForegroundColor Green
Write-Host "=====================================" -ForegroundColor Green

if ($started.Count -gt 0) {
    Write-Host "Started:" -ForegroundColor Cyan
    $started | ForEach-Object { Write-Host "  - $_" -ForegroundColor White }
}
if ($changed.Count -gt 0) {
    Write-Host "Recreated (changed):" -ForegroundColor Cyan
    $changed | ForEach-Object { Write-Host "  - $_" -ForegroundColor White }
}
if ($stopped.Count -gt 0) {
    Write-Host "Stopped:" -ForegroundColor Cyan
    $stopped | ForEach-Object { Write-Host "  - $_" -ForegroundColor White }
}
if ($started.Count -eq 0 -and $changed.Count -eq 0 -and $stopped.Count -eq 0) {
    Write-Host "No container changes detected." -ForegroundColor Gray
}

Write-Host "" 
Write-Host "Tip: view status with: docker compose -f docker-compose.yml -f docker-compose.dev.yml ps" -ForegroundColor Gray

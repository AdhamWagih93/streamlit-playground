# Best Streamlit Website - Development Startup Script (Windows/PowerShell)
# This script starts the full development stack using Docker Compose

param(
    [switch]$WithAI,      # Include Ollama service
    [switch]$WithTools,   # Include development tools (DB admin)
    [switch]$Full,        # Include all optional services
    [switch]$Detach,      # Run in background
    [switch]$Build        # Force rebuild images
)

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Best Streamlit Website - Dev Startup" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""

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

# Check if .env exists, if not copy from example
if (-not (Test-Path ".env")) {
    Write-Host "Creating .env file from .env.example..." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
    Write-Host "Please edit .env file with your configuration" -ForegroundColor Yellow
}

# Ensure data directory exists
if (-not (Test-Path "data")) {
    Write-Host "Creating data directory..." -ForegroundColor Green
    New-Item -ItemType Directory -Path "data" | Out-Null
}

# Build compose command
$composeCmd = "docker-compose -f docker-compose.yml -f docker-compose.dev.yml"

# Add profiles
$profiles = @()
if ($WithAI -or $Full) {
    $profiles += "ai"
}
if ($WithTools -or $Full) {
    $profiles += "tools"
}
if ($Full) {
    $profiles += "full"
}

# Build the command
$cmdArgs = @("up")

if ($Detach) {
    $cmdArgs += "-d"
}

if ($Build) {
    $cmdArgs += "--build"
}

if ($profiles.Count -gt 0) {
    foreach ($profile in $profiles) {
        $cmdArgs += "--profile"
        $cmdArgs += $profile
    }
}

Write-Host "Starting services..." -ForegroundColor Green
Write-Host "Command: $composeCmd $($cmdArgs -join ' ')" -ForegroundColor Gray
Write-Host ""

# Execute docker-compose
$env:COMPOSE_PROJECT_NAME = "bsw"
Invoke-Expression "$composeCmd $($cmdArgs -join ' ')"

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "=====================================" -ForegroundColor Green
    Write-Host "Services started successfully!" -ForegroundColor Green
    Write-Host "=====================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Access the application at:" -ForegroundColor Cyan
    Write-Host "  Streamlit UI:     http://localhost:8501" -ForegroundColor White
    Write-Host "  Scheduler MCP:    http://localhost:8010" -ForegroundColor White
    Write-Host "  Docker MCP:       http://localhost:8001" -ForegroundColor White
    Write-Host "  Jenkins MCP:      http://localhost:8002" -ForegroundColor White
    Write-Host "  Kubernetes MCP:   http://localhost:8003" -ForegroundColor White

    if ($WithTools -or $Full) {
        Write-Host "  DB Admin:         http://localhost:8090" -ForegroundColor White
    }

    if ($WithAI -or $Full) {
        Write-Host "  Ollama API:       http://localhost:11434" -ForegroundColor White
    }

    Write-Host ""
    Write-Host "Useful commands:" -ForegroundColor Cyan
    Write-Host "  View logs:        .\scripts\dev-logs.ps1" -ForegroundColor Gray
    Write-Host "  Stop services:    .\scripts\dev-stop.ps1" -ForegroundColor Gray
    Write-Host "  Reset data:       .\scripts\dev-reset.ps1" -ForegroundColor Gray
} else {
    Write-Host ""
    Write-Host "ERROR: Failed to start services" -ForegroundColor Red
    exit 1
}

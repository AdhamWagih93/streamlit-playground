# Best Streamlit Website - Development Stop Script (Windows/PowerShell)
# This script stops all running services

param(
    [switch]$Remove  # Remove containers and networks
)

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Stopping Best Streamlit Website" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""

# Navigate to repository root
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptPath
Set-Location $repoRoot

$env:COMPOSE_PROJECT_NAME = "bsw"
$composeCmd = "docker-compose -f docker-compose.yml -f docker-compose.dev.yml"

if ($Remove) {
    Write-Host "Stopping and removing containers..." -ForegroundColor Yellow
    Invoke-Expression "$composeCmd down --remove-orphans"
} else {
    Write-Host "Stopping containers..." -ForegroundColor Yellow
    Invoke-Expression "$composeCmd stop"
}

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "Services stopped successfully!" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "ERROR: Failed to stop services" -ForegroundColor Red
    exit 1
}

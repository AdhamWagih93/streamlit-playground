# Best Streamlit Website - Reset Script (Windows/PowerShell)
# This script resets the development environment

param(
    [switch]$KeepData  # Keep database files
)

Write-Host "=====================================" -ForegroundColor Red
Write-Host "Reset Development Environment" -ForegroundColor Red
Write-Host "=====================================" -ForegroundColor Red
Write-Host ""

# Navigate to repository root
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptPath
Set-Location $repoRoot

if (-not $KeepData) {
    Write-Host "WARNING: This will delete all data including databases!" -ForegroundColor Yellow
    $confirm = Read-Host "Are you sure? (yes/no)"

    if ($confirm -ne "yes") {
        Write-Host "Reset cancelled" -ForegroundColor Green
        exit 0
    }
}

Write-Host "Stopping containers..." -ForegroundColor Yellow
.\scripts\dev-stop.ps1 -Remove

Write-Host "Removing volumes..." -ForegroundColor Yellow
docker volume rm bsw-ollama-data -f 2>$null

if (-not $KeepData) {
    Write-Host "Removing data directory..." -ForegroundColor Yellow
    if (Test-Path "data") {
        Remove-Item -Recurse -Force "data"
    }
    New-Item -ItemType Directory -Path "data" | Out-Null
}

Write-Host ""
Write-Host "Environment reset complete!" -ForegroundColor Green
Write-Host "Run .\scripts\dev-start.ps1 to start fresh" -ForegroundColor Cyan

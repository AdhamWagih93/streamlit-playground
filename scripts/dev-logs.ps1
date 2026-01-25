# Best Streamlit Website - View Logs Script (Windows/PowerShell)
# This script displays logs from running services

param(
    [string]$Service = "",  # Specific service to view
    [switch]$Follow         # Follow log output
)

# Navigate to repository root
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptPath
Set-Location $repoRoot

$env:COMPOSE_PROJECT_NAME = "bsw"
$composeCmd = "docker-compose -f docker-compose.yml -f docker-compose.dev.yml"

$cmdArgs = "logs"

if ($Follow) {
    $cmdArgs += " -f"
}

$cmdArgs += " --tail=100"

if ($Service) {
    $cmdArgs += " $Service"
}

Write-Host "Viewing logs..." -ForegroundColor Cyan
Invoke-Expression "$composeCmd $cmdArgs"

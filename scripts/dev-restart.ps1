# Best Streamlit Website - Development Restart Script (Windows/PowerShell)
# Stops (optionally removes) the dev stack, then starts it again.

param(
    [switch]$WithAI,      # Include Ollama service
    [switch]$WithTools,   # Include development tools (DB admin)
    [switch]$Full,        # Include all optional services
    [switch]$Foreground,  # Run attached (stream logs in terminal)
    [switch]$Build,       # Force rebuild images
    [switch]$Remove       # Remove containers/networks before starting
)

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Restarting Best Streamlit Website" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""

# Build arg list for dev-start
$startArgs = @()
if ($WithAI) { $startArgs += "-WithAI" }
if ($WithTools) { $startArgs += "-WithTools" }
if ($Full) { $startArgs += "-Full" }
if ($Foreground) { $startArgs += "-Foreground" }
if ($Build) { $startArgs += "-Build" }

# Stop first
if ($Remove) {
    .\scripts\dev-stop.ps1 -Remove
} else {
    .\scripts\dev-stop.ps1
}

# Start
Write-Host "" 
Write-Host "Starting again..." -ForegroundColor Green
& .\scripts\dev-start.ps1 @startArgs

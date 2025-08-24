Param(
    [switch]$KillPython
)

# Start Streamlit from repo root using local .venv
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path "$ScriptRoot\.."
Set-Location $RepoRoot

# Optionally kill existing Python processes (useful if port is stuck)
if ($KillPython) {
    Write-Host "Killing existing python processes..."
    taskkill /F /IM python.exe /T | Out-Null 2>$null
}

$Activate = Join-Path $RepoRoot ".venv\Scripts\Activate"
if (-Not (Test-Path $Activate)) {
    Write-Host ".venv not found under repository. Creating new venv using system python..."
    python -m venv .venv
    if (-Not (Test-Path $Activate)) {
        Write-Error "Failed to create virtualenv. Ensure Python is installed and accessible as 'python'."
        exit 1
    }
    Write-Host "Installing requirements into new venv..."
    & $Activate; .venv\Scripts\pip install --upgrade pip; .venv\Scripts\pip install -r best-streamlit-website\requirements.txt
}

Write-Host "Activating virtual environment and starting Streamlit..."
& $Activate

# Start Streamlit
python -m streamlit run best-streamlit-website/app.py --server.headless true

@echo off
cd /d %~dp0\..
if not exist .venv (
    echo Creating virtualenv...
    python -m venv .venv
    .venv\Scripts\pip install --upgrade pip
    .venv\Scripts\pip install -r best-streamlit-website\requirements.txt
)
.venv\Scripts\activate
python -m streamlit run best-streamlit-website\app.py --server.headless true
pause

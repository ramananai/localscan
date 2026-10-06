@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3 -m venv .venv || (echo Python 3.10+ not found. Install from python.org and tick "Add to PATH". & pause & exit /b 1)
)
if not exist ".venv\.deps_installed" (
    echo Installing packages ^(first run only^)...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt && echo ok> ".venv\.deps_installed"
)
".venv\Scripts\python.exe" -m streamlit run app.py --browser.gatherUsageStats false
pause

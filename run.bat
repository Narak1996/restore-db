@echo off
title DB Restore Studio
cd /d "%~dp0"

echo ===================================================
echo             Starting DB Restore Studio
echo ===================================================

if not exist "venv\Scripts\python.exe" (
    echo [INFO] Creating Python virtual environment...
    python -m venv venv
    call venv\Scripts\activate.bat
    echo [INFO] Installing required packages...
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate.bat
)

echo [INFO] Launching Streamlit web application...
python -m streamlit run app.py
pause

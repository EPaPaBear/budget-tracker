@echo off
REM ============================================================
REM  Ledger - local budgeting app launcher (Windows)
REM ============================================================
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
    call ".venv\Scripts\activate.bat"
    echo Installing dependencies...
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
) else (
    call ".venv\Scripts\activate.bat"
)

echo.
echo Starting Ledger on http://127.0.0.1:8770
echo Press Ctrl+C to stop.
echo.

REM Open the browser after a short delay, then run the server
start "" cmd /c "timeout /t 2 >nul & start http://127.0.0.1:8770"

uvicorn app:app --port 8770 --reload

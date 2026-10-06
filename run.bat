@echo off
setlocal

REM ── AI-DLC Backend Server Startup Script ─────────────────────────────
REM Sets up PYTHONPATH for nested module directories and starts uvicorn

REM Python path (installed via winget)
set PYTHON_PATH=C:\Users\jainil_moontechnolab\AppData\Local\Programs\Python\Python312
set PATH=%PYTHON_PATH%;%PYTHON_PATH%\Scripts;%PATH%

REM Backend root
set BACKEND_DIR=%~dp0

REM PYTHONPATH: include backend root + all nested module directories
set PYTHONPATH=%BACKEND_DIR%;%BACKEND_DIR%llm-20260922T070723Z-1-001;%BACKEND_DIR%db_less_optimizer-20260922T070953Z-1-001;%BACKEND_DIR%validator-20260922T070723Z-1-001;%BACKEND_DIR%utils-20260922T070723Z-1-001

echo Starting AI-DLC Backend Server...
echo   Backend:  %BACKEND_DIR%
echo   Python:   %PYTHON_PATH%
echo   Port:     8000
echo   Rate Limit: 20 queries (testing)
echo─────────────────────────────────────────────────────

uvicorn api:app --host 0.0.0.0 --port 8000 --reload

endlocal
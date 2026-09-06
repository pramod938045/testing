@echo off
REM Start the Jira chatbot web UI on Windows. Double-click this file.
cd /d "%~dp0"

if not exist ".env" (
  echo.
  echo   No .env file found.
  echo   Copy .env.example to .env and fill in JIRA_BASE_URL, JIRA_EMAIL,
  echo   JIRA_API_TOKEN and ANTHROPIC_API_KEY, then run this again.
  echo.
  pause
  exit /b 1
)

echo Installing requirements (first run only, takes a minute)...
python -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo.
  echo   Install failed. Is Python 3.11+ installed and on your PATH?
  pause
  exit /b 1
)

REM Check the settings before starting, so problems are readable
REM instead of arriving as a stack trace.
python -m app.check
if errorlevel 1 (
  echo.
  pause
  exit /b 1
)

echo.
echo   Starting the chatbot. It will open at http://localhost:8000
echo   Give it a few seconds. Press Ctrl+C in this window to stop it.
echo.

REM Open the browser a few seconds AFTER the server starts listening,
REM otherwise the page loads before anything is there to answer it.
start "" cmd /c "timeout /t 5 >nul & start """" http://localhost:8000"

python -m uvicorn app.main:app --port 8000
echo.
echo   The server stopped. Any error above explains why.
pause

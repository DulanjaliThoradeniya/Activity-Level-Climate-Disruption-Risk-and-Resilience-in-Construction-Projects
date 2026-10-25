@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if errorlevel 1 (
  echo Python launcher "py" was not found. Install Python 3 and try again.
  pause
  exit /b 1
)

py NeuralProphet.py --serve --open-browser
if errorlevel 1 (
  echo.
  echo Climate Resist could not start. If Python dependencies are missing, run:
  echo   py -m pip install -r requirements.txt
  pause
)

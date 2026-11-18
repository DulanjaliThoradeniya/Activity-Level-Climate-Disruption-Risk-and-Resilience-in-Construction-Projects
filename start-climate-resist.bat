@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if errorlevel 1 (
  echo Python launcher "py" was not found. Install Python 3 and try again.
  pause
  exit /b 1
)

start "Climate Resist static server" /min py -m http.server 8000 --bind 127.0.0.1
start "C-RESIST climate model backend" /min py "Climate Model\Trained model\prophet_forecast.py" --serve --port 8765
powershell -NoProfile -Command "$ready = $false; for ($i = 0; $i -lt 20 -and -not $ready; $i++) { try { $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/' -TimeoutSec 1; $ready = $response.StatusCode -eq 200 } catch { Start-Sleep -Milliseconds 500 } }; if (-not $ready) { exit 1 }"
if errorlevel 1 (
  echo.
  echo Climate Resist could not start the local static file server.
  echo Install Python 3 or use the published GitHub Pages website.
  pause
  exit /b 1
)

start "" http://127.0.0.1:8000/

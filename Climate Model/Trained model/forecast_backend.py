import argparse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = Path(__file__).resolve().parent
MULTI_OUTPUT = PROJECT_ROOT / "data" / "climate-multi-model-forecasts.json"
STATUS_OUTPUT = PROJECT_ROOT / "data" / "climate-backend-status.json"
REFRESH_SECONDS = 24 * 60 * 60
MODEL_LOCK = threading.Lock()


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def is_fresh(path):
    return path.exists() and time.time() - path.stat().st_mtime < REFRESH_SECONDS


def save_status(payload):
    write_json(STATUS_OUTPUT, payload)


def run_builder(model_id, script):
    started = datetime.now(timezone.utc).isoformat()
    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=3600,
    )
    return {
        "status": "ready" if completed.returncode == 0 else "error",
        "startedAt": started,
        "completedAt": datetime.now(timezone.utc).isoformat(),
        "error": completed.stderr.strip()[-2000:] if completed.returncode else "",
    }


def rebuild_models():
    with MODEL_LOCK:
        history_update = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "update_climate_data.py"), "--refresh-recent"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=3600,
        )
        if history_update.returncode == 0:
            print("NEA history refreshed and replaced.", flush=True)
        else:
            print(f"NEA history refresh failed: {history_update.stderr.strip()[-2000:]}", flush=True)
        builders = (
            ("existing_model", MODEL_DIR / "build_prophet_forecasts.py"),
            ("xgboost", MODEL_DIR / "build_multi_model_forecasts.py"),
            ("remaining_models", MODEL_DIR / "build_remaining_climate_models.py"),
        )
        results = {model_id: run_builder(model_id, script) for model_id, script in builders}
        save_status({
            "status": "ready" if any(result["status"] == "ready" for result in results.values()) else "error",
            "completedAt": datetime.now(timezone.utc).isoformat(),
            "nextRunAt": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
            "intervalHours": 24,
            "models": results,
        })


def scheduler():
    if MULTI_OUTPUT.exists():
        save_status({
            "status": "ready",
            "completedAt": datetime.fromtimestamp(MULTI_OUTPUT.stat().st_mtime, tz=timezone.utc).isoformat(),
            "nextRunAt": None,
            "intervalHours": 24,
            "models": {
                "existing_model": {"status": "ready"},
                "xgboost": {"status": "ready"},
                "remaining_models": {"status": "ready"},
            },
        })


class BackendHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError) as error:
            print(f"Could not write response: {error}", flush=True)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        print(f"GET {self.path}", flush=True)
        request = urlsplit(self.path)
        if request.path == "/api/models/status":
            try:
                payload = json.loads(STATUS_OUTPUT.read_text(encoding="utf-8")) if STATUS_OUTPUT.exists() else {
                    "status": "starting",
                    "nextRunAt": None,
                    "intervalHours": 24,
                    "models": {},
                }
                self._send_json(payload)
            except Exception as error:
                self._send_json({"status": "error", "error": str(error)}, 503)
            return

        if request.path == "/api/model-forecast":
            query = parse_qs(request.query)
            model = query.get("model", [""])[0]
            station = query.get("station", [""])[0]
            target = query.get("target", [""])[0]
            try:
                payload = json.loads(MULTI_OUTPUT.read_text(encoding="utf-8"))
                result = payload["models"][model]["stations"][station]["targets"][target]
                self._send_json({
                    "model": model,
                    "station": station,
                    "target": target,
                    "result": result,
                })
            except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
                self._send_json({"error": "No saved forecast is available yet."}, 404)
            return

        self._send_json({"error": "Not found"}, 404)

    def log_message(self, format_string, *args):
        print("%s - %s" % (self.address_string(), format_string % args), flush=True)


def main():
    parser = argparse.ArgumentParser(description="C-RESIST 24-hour forecast backend.")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    canonical_backend = Path(__file__).with_name("prophet_forecast.py")
    os.execv(
        sys.executable,
        [sys.executable, str(canonical_backend), "--serve", "--port", str(args.port)],
    )


if __name__ == "__main__":
    main()

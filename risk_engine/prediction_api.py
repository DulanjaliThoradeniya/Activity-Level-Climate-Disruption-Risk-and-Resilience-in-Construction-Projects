"""Optional local-only FastAPI inference endpoint for the C-RESIST browser app."""

from __future__ import annotations

import argparse
import os
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .prediction_service import ActivityDisruptionPredictor


def create_app(
    *,
    profile: str | None = None,
    allow_synthetic_demo: bool | None = None,
) -> FastAPI:
    selected_profile = profile or os.environ.get(
        "C_RESIST_DELAY_MODEL_PROFILE", "real_research"
    )
    demo_allowed = (
        allow_synthetic_demo
        if allow_synthetic_demo is not None
        else os.environ.get("C_RESIST_ALLOW_SYNTHETIC_DEMO", "").lower()
        in {"1", "true", "yes"}
    )
    predictor: ActivityDisruptionPredictor | None = None
    predictor_error: str | None = None
    try:
        predictor = ActivityDisruptionPredictor(
            profile=selected_profile,
            allow_synthetic_demo=demo_allowed,
        )
    except (FileNotFoundError, PermissionError, ValueError, OSError) as error:
        predictor_error = str(error)

    app = FastAPI(
        title="C-RESIST Activity Disruption Prediction API",
        version="1.0.0",
        description=(
            "Separate productivity/delay estimation endpoint. It does not assess "
            "WSH risk and cannot authorize work."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://dulanjalithoradeniya.github.io"],
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ready" if predictor else "unavailable",
            "model_status": predictor.model_profile if predictor else None,
            "message": predictor_error,
            "real_safety_decision_use_permitted": False,
        }

    @app.post("/api/activity-disruption/predict")
    def predict(request: dict[str, Any]) -> dict[str, Any]:
        if predictor is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Activity disruption model unavailable. Existing illustrative "
                    "scenario mode remains available. " + str(predictor_error)
                ),
            )
        try:
            return predictor.predict(request)
        except (ValueError, KeyError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    return app


app = create_app()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the local-only C-RESIST activity disruption API."
    )
    parser.add_argument(
        "--profile",
        choices=("real_research", "synthetic_demo"),
        default="real_research",
    )
    parser.add_argument(
        "--allow-synthetic-demo",
        action="store_true",
        help="Explicitly allow a SYNTHETIC_DEMO model; output remains demo-only.",
    )
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    if args.profile == "synthetic_demo" and not args.allow_synthetic_demo:
        parser.error("synthetic_demo requires --allow-synthetic-demo")
    os.environ["C_RESIST_DELAY_MODEL_PROFILE"] = args.profile
    os.environ["C_RESIST_ALLOW_SYNTHETIC_DEMO"] = (
        "1" if args.allow_synthetic_demo else "0"
    )
    import uvicorn

    uvicorn.run(
        create_app(
            profile=args.profile,
            allow_synthetic_demo=args.allow_synthetic_demo,
        ),
        host="127.0.0.1",
        port=args.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

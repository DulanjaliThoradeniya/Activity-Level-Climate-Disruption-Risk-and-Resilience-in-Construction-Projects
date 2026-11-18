"""Build a project-specific prediction JSON bundle for static hosting."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .prediction_service import (
    ActivityDisruptionPredictor,
    DEFAULT_STATIC_BUNDLE,
    canonical_request,
)
from .train_delay_model import DEFAULT_OUTPUT_DIR


def build_static_bundle(
    request_path: str | Path,
    output_path: str | Path = DEFAULT_STATIC_BUNDLE,
    *,
    profile: str = "real_research",
    allow_synthetic_demo: bool = False,
    model_root: str | Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    source = Path(request_path).expanduser().resolve()
    request = json.loads(source.read_text(encoding="utf-8"))
    canonical = canonical_request(request)
    predictor = ActivityDisruptionPredictor(
        model_root=model_root,
        profile=profile,
        allow_synthetic_demo=allow_synthetic_demo,
    )
    prediction = predictor.predict(canonical)
    prediction["bundle_type"] = "project_specific_static_prediction_bundle"
    prediction["generated_from_request"] = source.name
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(prediction, indent=2, ensure_ascii=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return prediction


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a forecast- and project-specific static prediction JSON."
    )
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_STATIC_BUNDLE)
    parser.add_argument(
        "--profile",
        choices=("real_research", "synthetic_demo"),
        default="real_research",
    )
    parser.add_argument("--allow-synthetic-demo", action="store_true")
    parser.add_argument("--model-root", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    if args.profile == "synthetic_demo" and not args.allow_synthetic_demo:
        parser.error("synthetic_demo requires --allow-synthetic-demo")
    prediction = build_static_bundle(
        args.request,
        args.output,
        profile=args.profile,
        allow_synthetic_demo=args.allow_synthetic_demo,
        model_root=args.model_root,
    )
    print(
        f"Wrote {prediction['model_status']} prediction bundle for "
        f"{len(prediction['productivity_profiles'])} activities to {args.output}. "
        "GitHub Pages serves this JSON; it does not execute Python."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

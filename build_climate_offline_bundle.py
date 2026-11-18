import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FILES = (
    "data/climate-history.json",
    "data/climate-forecasts.json",
    "data/climate-multi-model-forecasts.json",
)


def build_bundle(root=ROOT):
    bundle = {}
    for name in FILES:
        payload = json.loads((root / name).read_text(encoding="utf-8"))
        if payload.get("schemaVersion") != 1:
            raise ValueError(f"{name} has an unsupported schema version.")
        bundle[name] = payload
    output = root / "data" / "climate-offline.js"
    temporary = output.with_suffix(".js.tmp")
    temporary.write_text(
        "window.CResistClimateOffline="
        + json.dumps(bundle, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
        + ";\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    return output


if __name__ == "__main__":
    print(f"Built {build_bundle()}")

#!/usr/bin/env bash
set -euo pipefail

mode=${1:?Expected bundled or refresh}
case "$mode" in bundled|refresh) ;; *) echo "::error::Unsupported release mode: $mode"; exit 1 ;; esac

stage=$(mktemp -d "${RUNNER_TEMP:?}/cresist-release.XXXXXX")
tar -C "${GITHUB_WORKSPACE:?}" --exclude=./.git --exclude=./.venv \
  --exclude=./.climate-cache --exclude=./.forecast-api-cache \
  --exclude='*/__pycache__' --exclude='*.pyc' -cf - . | tar -C "$stage" -xf -
cd "$stage"
export PYTHONDONTWRITEBYTECODE=1

python climate_history_validation.py
if [ "$mode" = refresh ]; then
  python update_climate_data.py --refresh-recent
  python climate_history_validation.py
fi
python "Climate Model/Trained model/build_prophet_forecasts.py"
python "Climate Model/Trained model/build_multi_model_forecasts.py"
python "Climate Model/Trained model/build_remaining_climate_models.py"
python validate_climate_release.py
python build_climate_offline_bundle.py
test -s index.html
echo "directory=$stage" >> "${GITHUB_OUTPUT:?}"

"""Verify the committed Brev LightGBM on CPU without retraining."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import lightgbm as lgb
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "artifacts/models/brev-scada-pooled-lgbm-20260201"


def main() -> None:
    metadata = json.loads((BUNDLE / "metadata.json").read_text(encoding="utf-8"))
    fixture = json.loads((BUNDLE / "cpu_fixture.json").read_text(encoding="utf-8"))
    model_path = BUNDLE / "model.txt"
    digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
    if digest != metadata["model_sha256"]:
        raise ValueError("model.txt SHA-256 differs from metadata")
    booster = lgb.Booster(model_file=str(model_path))
    if booster.feature_name() != metadata["feature_order"]:
        raise ValueError("LightGBM feature order differs from metadata")
    zone = ZoneInfo(metadata["source_timezone"])
    rows = []
    for point in fixture["input"]:
        timestamp = datetime.fromisoformat(point["target_start"].replace("Z", "+00:00")).astimezone(zone)
        wind = float(point["wind_speed_100m"])
        temperature = float(point["temperature_2m"])
        turbine = {"T1": 1, "T2": 2}[point["turbine_id"]]
        rows.append((wind, temperature, wind ** 2, wind ** 3, timestamp.hour,
                     timestamp.month, timestamp.timetuple().tm_yday, turbine))
    predictions = np.clip(booster.predict(np.asarray(rows, dtype=np.float64)), 0, 1)
    np.testing.assert_allclose(predictions, fixture["expected_normalized_power"],
                               rtol=0, atol=fixture["absolute_tolerance"])
    print(json.dumps({"model_id": metadata["model_id"], "sha256": digest,
                      "predictions": predictions.tolist(), "verified": True}, allow_nan=False))


if __name__ == "__main__":
    main()

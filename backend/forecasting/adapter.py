"""A small, auditable CPU model-bundle adapter."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from backend.replay import ReplayContext, validate_artifact_cutoffs

FEATURES = (
    "wind_u_100m", "wind_v_100m", "wind_speed_100m", "wind_u_10m",
    "wind_v_10m", "wind_speed_10m", "temperature_2m",
)


class ModelBlocked(RuntimeError):
    pass


def load_bundle(path: Path, model_id: str, context: ReplayContext) -> dict[str, Any]:
    if not path.is_file():
        raise ModelBlocked(f"registered model bundle is missing: {model_id}")
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelBlocked(f"model bundle cannot be loaded: {model_id}") from exc
    if bundle.get("model_id") != model_id:
        raise ModelBlocked("model bundle id mismatch")
    try:
        validate_artifact_cutoffs(bundle, context)
    except (TypeError, ValueError) as exc:
        raise ModelBlocked(str(exc)) from exc
    if tuple(bundle.get("feature_order", ())) != FEATURES:
        raise ModelBlocked("model feature schema is incompatible")
    coefficients = bundle.get("coefficients")
    if not isinstance(coefficients, dict) or set(coefficients) != set(FEATURES):
        raise ModelBlocked("model coefficients are incompatible")
    values = [bundle.get("intercept"), *coefficients.values()]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise ModelBlocked("model parameters must be finite numbers")
    return bundle


def predict(bundle: dict[str, Any], weather_points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for point in weather_points:
        value = float(bundle["intercept"])
        for feature in FEATURES:
            value += float(bundle["coefficients"][feature]) * float(point[feature])
        output.append({
            "turbine_id": point["turbine_id"],
            "target_start": point["target_start"],
            "target_end": point["target_end"],
            "normalized_power": min(1.0, max(0.0, value)),
        })
    return output

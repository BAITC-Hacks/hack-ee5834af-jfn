"""Small, auditable CPU adapters for registered model bundles."""

from __future__ import annotations

import hashlib
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


def _json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelBlocked(f"{label} cannot be loaded") from exc
    if not isinstance(value, dict):
        raise ModelBlocked(f"{label} must be an object")
    return value


def load_native_bundle(path: Path, model_id: str, context: ReplayContext) -> dict[str, Any]:
    """Validate and load a code-free native LightGBM directory on CPU."""
    metadata = _json_object(path / "metadata.json", "model metadata")
    schema = _json_object(path / "feature_schema.json", "model feature schema")
    if metadata.get("kind") != "lightgbm_native_v1" or metadata.get("model_id") != model_id:
        raise ModelBlocked("model bundle metadata is incompatible")
    try:
        validate_artifact_cutoffs(metadata, context)
    except (TypeError, ValueError) as exc:
        raise ModelBlocked(str(exc)) from exc
    feature_order = metadata.get("feature_order")
    if (not isinstance(feature_order, list) or not feature_order
            or not all(isinstance(name, str) and name for name in feature_order)
            or len(set(feature_order)) != len(feature_order)
            or schema.get("feature_order") != feature_order):
        raise ModelBlocked("model feature schema is incompatible")
    expected_sha = metadata.get("model_sha256")
    if not isinstance(expected_sha, str) or len(expected_sha) != 64:
        raise ModelBlocked("model SHA-256 metadata is incompatible")
    try:
        model_bytes = (path / "model.txt").read_bytes()
    except OSError as exc:
        raise ModelBlocked("model file cannot be loaded") from exc
    actual_sha = hashlib.sha256(model_bytes).hexdigest()
    if actual_sha != expected_sha:
        raise ModelBlocked("model.txt SHA-256 differs from metadata")
    try:
        import lightgbm as lgb
        booster = lgb.Booster(model_str=model_bytes.decode("utf-8"))
    except (ImportError, UnicodeDecodeError, ValueError) as exc:
        raise ModelBlocked("LightGBM model cannot be loaded") from exc
    if booster.feature_name() != feature_order:
        raise ModelBlocked("LightGBM feature order is incompatible")
    return {"kind": "lightgbm_native_v1", "booster": booster,
            "metadata": metadata, "model_sha256": actual_sha}


def load_bundle(path: Path, model_id: str, context: ReplayContext) -> dict[str, Any]:
    if path.is_dir():
        return load_native_bundle(path, model_id, context)
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
    if bundle.get("kind") == "lightgbm_native_v1":
        raise ModelBlocked("native LightGBM bundle requires a model-specific feature adapter")
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

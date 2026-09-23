"""Auditable CPU adapters for registered forecast model bundles."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from backend.replay import ReplayContext, parse_timestamp, validate_artifact_cutoffs

FEATURES = ("wind_u_100m", "wind_v_100m", "wind_speed_100m", "wind_u_10m", "wind_v_10m", "wind_speed_10m", "temperature_2m")
SCADA_FEATURES = ("wind_speed", "temperature", "wind_sq", "wind_cu", "hour", "month", "day_of_year", "turbine_id")
SCADA_WARNING = "Experimental input mapping: observed hourly SCADA wind and temperature were replaced with instantaneous GFS values; operational forecast accuracy is unmeasured."


class ModelBlocked(RuntimeError):
    pass


def _json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelBlocked(f"{label} cannot be loaded") from exc
    if not isinstance(value, dict):
        raise ModelBlocked(f"{label} must be an object")
    return value


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelBlocked(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ModelBlocked(f"{label} must be a finite number")
    return result


def _linear(path: Path, model_id: str, context: ReplayContext) -> dict[str, Any]:
    model = _json(path, "model bundle")
    if model.get("model_id") != model_id:
        raise ModelBlocked("model bundle id mismatch")
    try:
        validate_artifact_cutoffs(model, context)
    except (TypeError, ValueError) as exc:
        raise ModelBlocked(str(exc)) from exc
    if tuple(model.get("feature_order", ())) != FEATURES:
        raise ModelBlocked("model feature schema is incompatible")
    coefficients = model.get("coefficients")
    if not isinstance(coefficients, dict) or set(coefficients) != set(FEATURES):
        raise ModelBlocked("model coefficients are incompatible")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in [model.get("intercept"), *coefficients.values()]):
        raise ModelBlocked("model parameters must be finite numbers")
    return {"kind": "linear_json_v1", "bundle": model, "warning": None}


def _scada(path: Path, model_id: str, context: ReplayContext) -> dict[str, Any]:
    metadata, schema, manifest = (_json(path / name, label) for name, label in (("metadata.json", "model metadata"), ("feature_schema.json", "model feature schema"), ("data_manifest.json", "model manifest")))
    if metadata.get("kind") != "scada_lightgbm_v1" or metadata.get("model_id") != model_id:
        raise ModelBlocked("model bundle metadata is incompatible")
    try:
        validate_artifact_cutoffs(metadata, context)
        manifest_cutoffs = {key: manifest[key] for key in ("training_cutoff", "preprocessing_cutoff", "calibration_cutoff", "data_cutoff") if key in manifest}
        if manifest_cutoffs:
            validate_artifact_cutoffs({"training_cutoff": metadata["training_cutoff"], **manifest_cutoffs}, context)
    except (TypeError, ValueError) as exc:
        raise ModelBlocked(str(exc)) from exc
    if tuple(metadata.get("feature_order", ())) != SCADA_FEATURES or tuple(schema.get("feature_order", ())) != SCADA_FEATURES:
        raise ModelBlocked("model feature schema is incompatible")
    if metadata.get("source_timezone") != "Asia/Almaty" or metadata.get("weather_mapping") != {"wind_speed": "wind_speed_100m", "temperature": "temperature_2m"}:
        raise ModelBlocked("model weather mapping is incompatible")
    expected_sha = metadata.get("model_sha256")
    if not isinstance(expected_sha, str) or len(expected_sha) != 64 or manifest.get("model_sha256") != expected_sha:
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
    if tuple(booster.feature_name()) != SCADA_FEATURES:
        raise ModelBlocked("LightGBM feature order is incompatible")
    limitations = manifest.get("limitations")
    if not isinstance(limitations, list) or not all(isinstance(item, str) for item in limitations):
        raise ModelBlocked("model manifest limitations are incompatible")
    return {"kind": "scada_lightgbm_v1", "booster": booster, "metadata": metadata, "manifest": manifest, "model_sha256": actual_sha, "warning": SCADA_WARNING}


def load_bundle(path: Path, model_id: str, context: ReplayContext) -> dict[str, Any]:
    if path.is_file():
        return _linear(path, model_id, context)
    if path.is_dir():
        return _scada(path, model_id, context)
    raise ModelBlocked(f"registered model bundle is missing: {model_id}")


def _linear_predict(bundle: dict[str, Any], weather_points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    model, output = bundle["bundle"], []
    for point in weather_points:
        value = float(model["intercept"])
        for feature in FEATURES:
            value += float(model["coefficients"][feature]) * float(point[feature])
        output.append({"turbine_id": point["turbine_id"], "target_start": point["target_start"], "target_end": point["target_end"], "normalized_power": min(1.0, max(0.0, value))})
    return output


def _scada_predict(bundle: dict[str, Any], weather_points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        import numpy as np
    except ImportError as exc:
        raise ModelBlocked("numpy is required for LightGBM inference") from exc
    rows, zone = [], ZoneInfo("Asia/Almaty")
    for index, point in enumerate(weather_points):
        wind = _finite(point.get("wind_speed_100m"), f"weather point {index} wind_speed_100m")
        temperature = _finite(point.get("temperature_2m"), f"weather point {index} temperature_2m")
        turbine = {"T1": 1, "T2": 2}.get(point.get("turbine_id"))
        if turbine is None:
            raise ModelBlocked(f"weather point {index} turbine_id is incompatible")
        try:
            timestamp = parse_timestamp(point.get("target_start")).astimezone(zone)
        except (TypeError, ValueError) as exc:
            raise ModelBlocked(f"weather point {index} target_start is invalid") from exc
        row = (wind, temperature, wind ** 2, wind ** 3, timestamp.hour, timestamp.month, timestamp.timetuple().tm_yday, turbine)
        if not all(math.isfinite(float(value)) for value in row):
            raise ModelBlocked(f"weather point {index} derived features must be finite")
        rows.append(row)
    try:
        values = bundle["booster"].predict(np.asarray(rows, dtype=np.float64))
    except (TypeError, ValueError) as exc:
        raise ModelBlocked("LightGBM prediction failed") from exc
    output = []
    for point, value in zip(weather_points, values, strict=True):
        value = _finite(value, "LightGBM prediction")
        output.append({"turbine_id": point["turbine_id"], "target_start": point["target_start"], "target_end": point["target_end"], "normalized_power": min(1.0, max(0.0, value))})
    return output


def predict(bundle: dict[str, Any], weather_points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if bundle.get("kind") == "linear_json_v1":
        return _linear_predict(bundle, weather_points)
    if bundle.get("kind") == "scada_lightgbm_v1":
        return _scada_predict(bundle, weather_points)
    raise ModelBlocked("model kind is incompatible")

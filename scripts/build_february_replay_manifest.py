#!/usr/bin/env python3
"""Build the truthful daily February replay coverage manifest."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.forecasting import load_bundle, predict
from backend.replay import ReplayContext, validate_weather_snapshot
from backend.workflow import ForecastService

ROOT = Path(__file__).parents[1]
MODEL_ID = "gfs-pooled-lgbm-mvp-20260131"
MODEL_DIR = ROOT / "artifacts/models" / MODEL_ID
FIXTURES = ROOT / "data/fixtures"
OUTPUT = ROOT / "february-replay-manifest.json"
POINTS_DIR = ROOT / "artifacts/replays"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    metadata = json.loads((MODEL_DIR / "metadata.json").read_text(encoding="utf-8"))
    feature_schema_sha = digest(MODEL_DIR / "feature_schema.json")
    model_bundle_sha = ForecastService.model_fingerprint(MODEL_DIR)
    entries = []
    first = datetime(2026, 1, 31, 6, tzinfo=timezone.utc)
    POINTS_DIR.mkdir(parents=True, exist_ok=True)
    for day in range(29):
        origin = first + timedelta(days=day)
        stamp = origin.strftime("%Y%m%dT%H%M%SZ")
        snapshot_path = FIXTURES / f"noaa-gfs-{stamp}-h48.json"
        base = {
            "as_of": origin.isoformat().replace("+00:00", "Z"),
            "horizon_hours": 48,
            "expected_point_count": 96,
            "model_id": MODEL_ID,
            "model_kind": metadata["kind"],
            "model_training_cutoff": metadata["training_cutoff"],
            "model_sha256": metadata["model_sha256"],
            "model_bundle_sha256": model_bundle_sha,
            "feature_schema_sha256": feature_schema_sha,
            "execution_mode": "deterministic_numeric_replay",
            "agent_execution": "not_run",
            "publication": "not_requested",
            "published": False,
        }
        if not snapshot_path.is_file():
            entries.append({**base, "status": "MISSING_WEATHER", "point_count": 0,
                            "reason": f"exact archived GFS fixture is not registered: {snapshot_path.relative_to(ROOT)}"})
            continue
        context = ReplayContext(origin, 48)
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        validate_weather_snapshot(snapshot, context)
        bundle = load_bundle(MODEL_DIR, MODEL_ID, context)
        points = predict(bundle, snapshot["points"], snapshot["init_time"])
        if len(points) != 96 or any(not math.isfinite(p["normalized_power"]) or not 0 <= p["normalized_power"] <= 1 for p in points):
            raise RuntimeError(f"invalid numerical replay output for {stamp}")
        artifact = POINTS_DIR / f"{stamp}-h48.json"
        payload = {"run_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"mangust:{MODEL_ID}:{origin.isoformat()}:48")),
                   "as_of": base["as_of"], "horizon_hours": 48, "model_id": MODEL_ID,
                   "weather_snapshot_sha256": digest(snapshot_path), "points": points}
        artifact.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        entries.append({**base, "status": "REAL", "run_status": "COMPUTED",
                        "numerical_validation": "SUCCEEDED",
                        "run_id": payload["run_id"], "point_count": len(points),
                        "snapshot_path": str(snapshot_path.relative_to(ROOT)),
                        "snapshot_sha256": payload["weather_snapshot_sha256"],
                        "weather_init_time": snapshot["init_time"],
                        "weather_available_at": snapshot["available_at"],
                        "target_start_min": min(p["target_start"] for p in points),
                        "target_end_max": max(p["target_end"] for p in points),
                        "forecast_artifact": str(artifact.relative_to(ROOT)),
                        "forecast_artifact_sha256": digest(artifact)})
    complete = all(entry["status"] == "REAL" for entry in entries)
    manifest = {"schema_version": 1, "complete": complete,
                "requested_origin_count": 29,
                "real_origin_count": sum(e["status"] == "REAL" for e in entries),
                "missing_weather_count": sum(e["status"] == "MISSING_WEATHER" for e in entries),
                "first_origin": entries[0]["as_of"], "last_origin": entries[-1]["as_of"],
                "final_target_end": "2026-03-02T06:00:00Z", "entries": entries}
    OUTPUT.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

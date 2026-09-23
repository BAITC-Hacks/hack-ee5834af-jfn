"""Chronological, purged evaluation of archived-GFS power forecasts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.forecasting.gfs_features import FEATURE_ORDER, TIMEZONE
from backend.replay import ReplayContext, parse_timestamp
from backend.forecasting import load_bundle, predict
from ml.train_brev import sha256, strict_json

BUCKETS = ((1, 6), (7, 12), (13, 24), (25, 36), (37, 48))
SCADA_REFERENCE_MAE = 0.030807364773109743


def split_rows(frame: pd.DataFrame, validation_start: datetime,
               final_cutoff: datetime) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    if validation_start >= final_cutoff:
        raise ValueError("validation_start must precede final_cutoff")
    origin = pd.to_datetime(frame.origin_time, utc=True)
    available = pd.to_datetime(frame.label_available_at, utc=True)
    if origin.isna().any() or available.isna().any():
        raise ValueError("missing origin or label availability time")
    train = frame.loc[(origin < validation_start) & (available <= validation_start)].copy()
    valid = frame.loc[(origin >= validation_start) & (origin < final_cutoff) &
                      (available <= final_cutoff)].copy()
    final = frame.loc[(origin < final_cutoff) & (available <= final_cutoff)].copy()
    if train.empty or valid.empty or final.empty:
        raise ValueError("train, validation and final sets must each contain rows")
    if set(train.origin_time) & set(valid.origin_time):
        raise ValueError("train and validation origins overlap")
    if pd.to_datetime(train.label_available_at, utc=True).max() > pd.to_datetime(valid.origin_time, utc=True).min():
        raise ValueError("training label crosses first validation origin")
    for subset_name, subset in (("train", train), ("validation", valid), ("final", final)):
        for tid in (1.0, 2.0):
            if not subset.turbine_id.eq(tid).any():
                raise ValueError(f"{subset_name} has no turbine {int(tid)} rows")
    keys = list(zip(valid.origin_time, valid.target_time, valid.turbine_id, strict=True))
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate validation origin/target/turbine keys")
    meta = {
        "validation_start": validation_start.isoformat().replace("+00:00", "Z"),
        "final_cutoff": final_cutoff.isoformat().replace("+00:00", "Z"),
        "purge_rule": "training labels available by validation_start; validation labels available by final_cutoff",
        "train_rows": len(train), "validation_rows": len(valid), "final_rows": len(final),
        "train_origins": train.origin_time.nunique(), "validation_origins": valid.origin_time.nunique(),
        "train_max_label_available_at": pd.to_datetime(train.label_available_at, utc=True).max().isoformat(),
        "validation_max_label_available_at": pd.to_datetime(valid.label_available_at, utc=True).max().isoformat(),
        "validation_key_sha256": hashlib.sha256(strict_json(keys).encode()).hexdigest(),
        "purged_train_rows": int(((origin < validation_start) & (available > validation_start)).sum()),
        "excluded_validation_late_labels": int(((origin >= validation_start) & (origin < final_cutoff) & (available > final_cutoff)).sum()),
    }
    return train, valid, final, meta


def feature_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    if not set(FEATURE_ORDER).issubset(frame.columns):
        raise ValueError("dataset misses GFS features")
    matrix = frame.loc[:, list(FEATURE_ORDER)].astype("float64")
    if not np.isfinite(matrix.to_numpy()).all():
        raise ValueError("non-finite GFS features")
    return matrix


def new_model() -> lgb.LGBMRegressor:
    return lgb.LGBMRegressor(n_estimators=300, learning_rate=0.035, num_leaves=15,
                             min_child_samples=30, reg_lambda=2.0, random_state=42,
                             n_jobs=2, verbosity=-1)


def fit_curve(train: pd.DataFrame) -> dict:
    curve = {}
    for turbine in (1, 2):
        part = train.loc[train.turbine_id.eq(turbine)]
        bins = np.floor(part.gfs_wind_speed_100m.to_numpy()).astype(int)
        curve[turbine] = {"bins": {int(b): float(np.median(part.target_power.to_numpy()[bins == b])) for b in np.unique(bins)},
                          "fallback": float(part.target_power.median())}
    return curve


def predict_curve(curve: dict, frame: pd.DataFrame) -> np.ndarray:
    result = []
    for row in frame.itertuples(index=False):
        spec = curve[int(row.turbine_id)]
        result.append(spec["bins"].get(int(np.floor(row.gfs_wind_speed_100m)), spec["fallback"]))
    return np.clip(np.asarray(result, dtype=float), 0, 1)


def score(actual: np.ndarray, predicted: np.ndarray) -> dict:
    if len(actual) == 0:
        return {"rows": 0, "mae": None, "rmse": None, "r2": None,
                "r2_reason": "no rows"}
    predicted = np.clip(np.asarray(predicted, dtype=float), 0, 1)
    if not np.isfinite(predicted).all():
        raise ValueError("non-finite predictions")
    error = actual - predicted
    denom = float(np.sum((actual - np.mean(actual)) ** 2))
    r2 = None if len(actual) < 2 or denom == 0 else 1 - float(np.sum(error ** 2)) / denom
    return {"rows": int(len(actual)), "mae": float(np.mean(np.abs(error))),
            "rmse": float(np.sqrt(np.mean(error ** 2))), "r2": r2,
            "r2_reason": ("fewer than 2 rows or constant target" if r2 is None else None)}


def metrics(valid: pd.DataFrame, predicted: np.ndarray) -> dict:
    actual = valid.target_power.to_numpy(dtype=float)
    result = {"overall": score(actual, predicted)}
    for turbine in (1, 2):
        mask = valid.turbine_id.eq(turbine).to_numpy()
        result[f"turbine_{turbine}"] = score(actual[mask], predicted[mask])
    for lower, upper in BUCKETS:
        mask = valid.origin_lead_hours.between(lower - 1, upper - 1).to_numpy()
        result[f"forecast_hour_{lower}_{upper}"] = score(actual[mask], predicted[mask])
    return result


def save_curve_bundle(out_dir: Path, curve: dict, source_manifest: dict,
                      dataset_dir: Path, split: dict, summary: dict,
                      final_cutoff: datetime, first_required_origin: datetime) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    curve_text = strict_json({str(tid): {"bins": {str(key): value for key, value in spec["bins"].items()},
                                        "fallback": spec["fallback"]}
                              for tid, spec in curve.items()}) + "\n"
    (out_dir / "model.txt").write_bytes(curve_text.encode("utf-8"))
    model_hash = sha256(out_dir / "model.txt")
    cutoff = final_cutoff.isoformat().replace("+00:00", "Z")
    feature_order = ["gfs_wind_speed_100m", "turbine_id"]
    limitations = ["Retrospective point-in-time simulation; artifact did not exist at historical origins.",
                   "Validated on four sparse January 2026 forecast origins only.",
                   "SCADA publication delay is unverified and assumed zero.",
                   "Nearest GFS grid point is about 12 km from the turbines."]
    metadata = {"model_id": out_dir.name, "kind": "gfs_power_curve_v1",
                "training_cutoff": cutoff, "feature_order": feature_order,
                "source_weather_provider": "noaa_gfs", "source_weather_model": "GFS 0.25-degree",
                "supported_forecast_horizon_hours": 48,
                "source_timezone": TIMEZONE, "origin_schedule": source_manifest["origin_policy"],
                "target_aggregation": source_manifest["target_aggregation"],
                "model_sha256": model_hash, "limitations": limitations}
    schema = {"kind": "gfs_power_curve_v1", "feature_order": feature_order,
              "wind_bin_rule": "floor(gfs_wind_speed_100m), turbine-specific median power; unseen bin uses turbine median"}
    manifest = {"training_cutoff": cutoff, "model_sha256": model_hash,
                "dataset_sha256": sha256(dataset_dir / "training_rows.csv"),
                "source_manifest_sha256": sha256(dataset_dir / "manifest.json"),
                "first_required_origin": first_required_origin.isoformat().replace("+00:00", "Z"),
                "split": split, "limitations": limitations}
    curve_metrics = {"metrics_kind": summary["metrics_kind"], "split": split,
                     "candidate_validation": summary["candidate_validation"]["power_curve_gfs"],
                     "validation_winner": summary["validation_winner"],
                     "exported_model_refit": True,
                     "exported_model_has_independent_holdout_score": False}
    for name, obj in (("metadata.json", metadata), ("feature_schema.json", schema),
                      ("data_manifest.json", manifest), ("metrics_summary.json", curve_metrics)):
        (out_dir / name).write_bytes((strict_json(obj) + "\n").encode("utf-8"))
    names = ("model.txt", "metadata.json", "feature_schema.json", "data_manifest.json", "metrics_summary.json")
    (out_dir / "checksums.sha256").write_bytes("".join(f"{sha256(out_dir / name)}  {name}\n" for name in names).encode("ascii"))


def train(dataset_dir: Path, out_dir: Path, validation_start: datetime,
          final_cutoff: datetime, first_required_origin: datetime) -> dict:
    if final_cutoff >= first_required_origin:
        raise ValueError("final cutoff must be before first required forecast origin")
    source_manifest = json.loads((dataset_dir / "manifest.json").read_text(encoding="utf-8"))
    dataset_path = dataset_dir / "training_rows.csv"
    if sha256(dataset_path) != source_manifest["dataset_sha256"]:
        raise ValueError("training dataset checksum differs from manifest")
    if tuple(source_manifest["feature_order"]) != FEATURE_ORDER:
        raise ValueError("dataset feature schema differs from serving schema")
    for origin_text, status in source_manifest["origins"].items():
        if status["status"] != "complete":
            continue
        snapshot_path = (dataset_dir / status["snapshot_relpath"]).resolve()
        if not snapshot_path.is_relative_to(dataset_dir.resolve()) or sha256(snapshot_path) != status["snapshot_sha256"]:
            raise ValueError(f"archived GFS snapshot checksum differs for {origin_text}")
        from backend.replay import validate_weather_snapshot
        validate_weather_snapshot(json.loads(snapshot_path.read_text(encoding="utf-8")),
                                  ReplayContext(parse_timestamp(origin_text), 48))
    frame = pd.read_csv(dataset_path)
    train_rows, valid, final, split = split_rows(frame, validation_start, final_cutoff)
    model = new_model()
    model.fit(feature_matrix(train_rows), train_rows.target_power)
    curve = fit_curve(train_rows)
    predictions = {
        "power_curve_gfs": predict_curve(curve, valid),
        "pooled_gfs_lightgbm": np.clip(model.predict(feature_matrix(valid)), 0, 1),
    }
    scores = {name: metrics(valid, values) for name, values in predictions.items()}
    winner = min(predictions, key=lambda name: (scores[name]["overall"]["mae"], name != "power_curve_gfs"))
    final_model = new_model()
    final_model.fit(feature_matrix(final), final.target_power)
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "model.txt"
    final_model.booster_.save_model(str(model_path))
    model_id = out_dir.name
    model_hash = sha256(model_path)
    cutoff_text = final_cutoff.isoformat().replace("+00:00", "Z")
    metadata = {
        "model_id": model_id, "kind": "gfs_lightgbm_v1",
        "training_cutoff": cutoff_text, "feature_order": list(FEATURE_ORDER),
        "source_weather_provider": "noaa_gfs", "source_weather_model": "GFS 0.25-degree",
        "origin_schedule": source_manifest["origin_policy"],
        "supported_forecast_horizon_hours": 48,
        "source_timezone": TIMEZONE, "target_aggregation": source_manifest["target_aggregation"],
        "model_sha256": model_hash,
        "limitations": ["Retrospective point-in-time simulation; model was trained after historical origins.",
                        "SCADA publication delay is unverified and currently assumed zero.",
                        "Sparse weekly forecast origins and nearest GFS grid point about 12 km from turbines."],
    }
    schema = {"kind": "gfs_lightgbm_v1", "feature_order": list(FEATURE_ORDER),
              "forecast_hour_definition": "origin_lead_hours is 0 for first target; score bucket is origin_lead_hours + 1",
              "gfs_lead_definition": "gfs_lead_hours is target_start minus GFS init_time in hours",
              "weather_fields": list(FEATURE_ORDER[:7])}
    manifest = {
        "dataset_sha256": sha256(dataset_path), "source_manifest_sha256": sha256(dataset_dir / "manifest.json"),
        "model_sha256": model_hash, "training_cutoff": cutoff_text,
        "first_required_origin": first_required_origin.isoformat().replace("+00:00", "Z"),
        "split": split, "limitations": metadata["limitations"],
        "source_origins_complete": source_manifest["origins_complete"],
        "source_gfs_cycles": len(source_manifest["gfs_cycles"]),
    }
    summary = {"metrics_kind": "archived_operational_gfs_retrospective_validation",
               "split": split, "validation_origin_min": valid.origin_time.min(),
               "validation_origin_max": valid.origin_time.max(),
               "candidate_validation": scores, "selection_rule": "lowest overall validation MAE; baseline wins exact tie",
               "validation_winner": winner,
               "scada_weather_conditional_reference": {"mae": SCADA_REFERENCE_MAE,
                  "warning": "Different validation population and observed target-time weather; not operational forecast accuracy or a like-for-like winner candidate."},
               "exported_model": "pooled_gfs_lightgbm",
               "exported_model_refit": True,
               "exported_model_has_independent_holdout_score": False}
    for name, obj in (("metadata.json", metadata), ("feature_schema.json", schema),
                      ("data_manifest.json", manifest), ("metrics_summary.json", summary)):
        (out_dir / name).write_bytes((strict_json(obj) + "\n").encode("utf-8"))
    files = ("model.txt", "metadata.json", "feature_schema.json", "data_manifest.json", "metrics_summary.json")
    (out_dir / "checksums.sha256").write_bytes("".join(f"{sha256(out_dir / name)}  {name}\n" for name in files).encode("ascii"))
    curve_name = out_dir.name.replace("gfs-pooled-lgbm", "gfs-power-curve")
    if curve_name == out_dir.name:
        curve_name += "-power-curve"
    save_curve_bundle(out_dir.parent / curve_name, fit_curve(final), source_manifest,
                      dataset_dir, split, summary, final_cutoff, first_required_origin)
    # The final model has a post-validation cutoff, so verify it at a later
    # replay origin using a complete archived snapshot when one is supplied
    # separately by the integration test. Here verify the CPU model and order.
    bundle = load_bundle(out_dir, model_id, ReplayContext(first_required_origin, 48))
    if tuple(bundle["booster"].feature_name()) != FEATURE_ORDER:
        raise ValueError("saved CPU model feature order differs")
    sample = feature_matrix(final.head(8))
    before = np.clip(final_model.predict(sample), 0, 1)
    after = np.clip(bundle["booster"].predict(sample), 0, 1)
    if not np.allclose(before, after, atol=1e-12, rtol=0):
        raise ValueError("saved CPU model does not reproduce predictions")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--validation-start", default="2026-01-01T00:00:00Z")
    parser.add_argument("--final-cutoff", default="2026-01-30T18:00:00Z")
    parser.add_argument("--first-required-origin", default="2026-01-31T06:00:00Z",
                        help="Assumed first 06:00 UTC operational origin on 31 Jan; override when schedule is known")
    args = parser.parse_args()
    summary = train(args.dataset_dir, args.out_dir, parse_timestamp(args.validation_start),
                    parse_timestamp(args.final_cutoff), parse_timestamp(args.first_required_origin))
    print(strict_json(summary))


if __name__ == "__main__":
    main()

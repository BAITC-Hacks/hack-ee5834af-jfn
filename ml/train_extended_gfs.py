"""Three-period, point-in-time GFS experiment with an untouched January holdout.

The selected candidate is chosen on December only. January scores describe the
pre-refit candidate; the exported bundle is refit through the declared cutoff.
"""

from __future__ import annotations

import argparse
from datetime import timedelta
import hashlib
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from backend.forecasting import load_bundle, predict
from backend.forecasting.gfs_features import FEATURE_ORDER, TIMEZONE
from backend.replay import ReplayContext, parse_timestamp, validate_weather_snapshot
from ml.train_brev import sha256, strict_json
from ml.train_gfs import fit_curve, predict_curve, feature_matrix, metrics

ROOT = Path(__file__).resolve().parents[1]
TUNE_START = pd.Timestamp("2025-12-01T00:00:00Z")
HOLDOUT_START = pd.Timestamp("2026-01-01T00:00:00Z")
FINAL_CUTOFF = pd.Timestamp("2026-01-30T18:00:00Z")
FIRST_ORIGIN = parse_timestamp("2026-01-31T06:00:00Z")
PARAMS = {
    "lgbm_strong_reg": dict(n_estimators=100, learning_rate=0.04, num_leaves=5,
                            min_child_samples=80, reg_lambda=30.0, min_split_gain=0.02),
    "lgbm_medium_reg": dict(n_estimators=180, learning_rate=0.035, num_leaves=7,
                            min_child_samples=60, reg_lambda=10.0, min_split_gain=0.01),
    "lgbm_feature_fraction": dict(n_estimators=130, learning_rate=0.035, num_leaves=5,
                                  min_child_samples=100, reg_lambda=20.0,
                                  feature_fraction=0.55),
}


def model_for(name: str) -> lgb.LGBMRegressor:
    return lgb.LGBMRegressor(**PARAMS[name], random_state=42, n_jobs=2, verbosity=-1)


def subsets(frame: pd.DataFrame, delay: float):
    origin = pd.to_datetime(frame.origin_time, utc=True)
    available = pd.to_datetime(frame.target_end, utc=True) + pd.Timedelta(hours=delay)
    # The target hour must end and the label must be available before the next period.
    early = frame.loc[(origin < TUNE_START) & (available <= TUNE_START)].copy()
    tune = frame.loc[(origin >= TUNE_START) & (origin < HOLDOUT_START) &
                     (available <= HOLDOUT_START)].copy()
    holdout = frame.loc[(origin >= HOLDOUT_START) & (origin < FINAL_CUTOFF) &
                        (available <= FINAL_CUTOFF)].copy()
    refit = frame.loc[(origin < FINAL_CUTOFF) & (available <= FINAL_CUTOFF)].copy()
    return early, tune, holdout, refit


def fit_predict(name: str, train: pd.DataFrame, target: pd.DataFrame):
    if name == "power_curve_gfs":
        return predict_curve(fit_curve(train), target)
    model = model_for(name)
    model.fit(feature_matrix(train), train.target_power)
    return np.clip(model.predict(feature_matrix(target)), 0, 1)


def score_candidates(train: pd.DataFrame, target: pd.DataFrame):
    return {name: metrics(target, fit_predict(name, train, target))
            for name in ("power_curve_gfs", *PARAMS)}


def previous_curve_comparison(previous: pd.DataFrame, expanded_train: pd.DataFrame,
                              holdout: pd.DataFrame):
    origin = pd.to_datetime(previous.origin_time, utc=True)
    available = pd.to_datetime(previous.label_available_at, utc=True)
    previous_train = previous.loc[(origin < HOLDOUT_START) & (available <= HOLDOUT_START)].copy()
    previous_january = previous.loc[(origin >= HOLDOUT_START) & (origin < FINAL_CUTOFF) &
                                    (available <= FINAL_CUTOFF)].copy()
    common = holdout.loc[holdout.origin_time.isin(previous_january.origin_time.unique())].copy()
    keys = ["origin_time", "target_time", "turbine_id"]
    if set(map(tuple, common[keys].to_numpy())) != set(map(tuple, previous_january[keys].to_numpy())):
        raise ValueError("previous and expanded January key sets differ")
    old_labels = previous_january.set_index(keys).target_power.sort_index()
    new_labels = common.set_index(keys).target_power.sort_index()
    if not old_labels.equals(new_labels):
        raise ValueError("previous and expanded January labels differ")
    return {
        "comparison_rule": "Both power curves fit before January; no January rows in either fit.",
        "previous_train_origins": previous_train.origin_time.nunique(),
        "expanded_train_origins": expanded_train.origin_time.nunique(),
        "common_4_origins": {
            "previous_curve": metrics(common, predict_curve(fit_curve(previous_train), common)),
            "expanded_curve": metrics(common, predict_curve(fit_curve(expanded_train), common)),
        },
        "all_9_origins": {
            "previous_curve": metrics(holdout, predict_curve(fit_curve(previous_train), holdout)),
            "expanded_curve": metrics(holdout, predict_curve(fit_curve(expanded_train), holdout)),
        },
    }


def audit_dataset(dataset: Path, manifest: dict, frame: pd.DataFrame):
    if sha256(dataset / "training_rows.csv") != manifest["dataset_sha256"]:
        raise ValueError("dataset SHA-256 mismatch")
    if tuple(manifest["feature_order"]) != FEATURE_ORDER:
        raise ValueError("serving feature order mismatch")
    if manifest["scada_assumptions"]["scada_delay_hours"] != 0:
        raise ValueError("experiment expects explicit zero-hour SCADA assumption")
    for text, status in manifest["origins"].items():
        if status["status"] != "complete":
            continue
        snapshot_path = (dataset / status["snapshot_relpath"]).resolve()
        if not snapshot_path.is_relative_to(dataset.resolve()) or sha256(snapshot_path) != status["snapshot_sha256"]:
            raise ValueError(f"snapshot SHA-256 mismatch: {text}")
        validate_weather_snapshot(json.loads(snapshot_path.read_text(encoding="utf-8")),
                                  ReplayContext(parse_timestamp(text), 48))
    keys = list(zip(frame.origin_time, frame.target_time, frame.turbine_id, strict=True))
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate origin/target/turbine keys")
    if (pd.to_datetime(frame.gfs_available_at, utc=True) > pd.to_datetime(frame.origin_time, utc=True)).any():
        raise ValueError("GFS became available after forecast origin")
    if (pd.to_datetime(frame.target_end, utc=True) <= pd.to_datetime(frame.target_time, utc=True)).any():
        raise ValueError("invalid target interval")


def write_bundle(out: Path, winner: str, refit: pd.DataFrame, source: Path,
                 manifest: dict, result: dict):
    out.mkdir(parents=True, exist_ok=True)
    if winner == "power_curve_gfs":
        curve = fit_curve(refit)
        payload = {str(tid): {"bins": {str(k): v for k, v in spec["bins"].items()},
                              "fallback": spec["fallback"]} for tid, spec in curve.items()}
        (out / "model.txt").write_bytes((strict_json(payload) + "\n").encode("utf-8"))
        kind, order = "gfs_power_curve_v1", ["gfs_wind_speed_100m", "turbine_id"]
    else:
        model = model_for(winner)
        model.fit(feature_matrix(refit), refit.target_power)
        model.booster_.save_model(str(out / "model.txt"))
        kind, order = "gfs_lightgbm_v1", list(FEATURE_ORDER)
    digest = sha256(out / "model.txt")
    cutoff = FINAL_CUTOFF.isoformat().replace("+00:00", "Z")
    limitations = ["Retrospective archived GFS evaluation; artifact was trained later.",
                   "SCADA publication latency is unknown; primary assumption is zero hours.",
                   "January metrics describe the pre-refit candidate, not the exported refit bundle.",
                   "No February SCADA labels were provided; February accuracy is unknown.",
                   "Stops and curtailment cannot be conclusively classified from supplied CSV columns."]
    metadata = {"model_id": out.name, "kind": kind, "training_cutoff": cutoff,
                "feature_order": order, "source_weather_provider": "noaa_gfs",
                "source_weather_model": "GFS 0.25-degree",
                "supported_forecast_horizon_hours": 48, "source_timezone": TIMEZONE,
                "origin_schedule": manifest["origin_policy"],
                "target_aggregation": manifest["target_aggregation"],
                "model_sha256": digest, "limitations": limitations}
    schema = {"kind": kind, "feature_order": order,
              "forecast_fields": list(FEATURE_ORDER[:7]),
              "weather_semantics": "instantaneous GFS at target_start; power is following-hour mean"}
    data_manifest = {"training_cutoff": cutoff, "first_required_origin": FIRST_ORIGIN.isoformat().replace("+00:00", "Z"),
                     "model_sha256": digest, "dataset_sha256": sha256(source / "training_rows.csv"),
                     "source_manifest_sha256": sha256(source / "manifest.json"),
                     "scada_csv_sha256": manifest["scada_csv_sha256"],
                     "refit_rows": len(refit), "refit_origins": refit.origin_time.nunique(),
                     "limitations": limitations}
    summary = {**result, "validation_winner": "pooled_gfs_lightgbm" if kind == "gfs_lightgbm_v1" else "power_curve_gfs",
               "exported_model_refit": True, "exported_model_has_independent_holdout_score": False}
    for name, obj in (("metadata.json", metadata), ("feature_schema.json", schema),
                      ("data_manifest.json", data_manifest), ("metrics_summary.json", summary)):
        (out / name).write_bytes((strict_json(obj) + "\n").encode("utf-8"))
    names = ("model.txt", "metadata.json", "feature_schema.json", "data_manifest.json", "metrics_summary.json")
    (out / "checksums.sha256").write_bytes("".join(f"{sha256(out / name)}  {name}\n" for name in names).encode("ascii"))
    bundle = load_bundle(out, out.name, ReplayContext(FIRST_ORIGIN, 48))
    snapshot = json.loads((ROOT / "data/fixtures/noaa-gfs-20260206T060000Z-h48.json").read_text(encoding="utf-8"))
    check_context = ReplayContext(parse_timestamp("2026-02-06T06:00:00Z"), 48)
    check_bundle = load_bundle(out, out.name, check_context)
    predictions = predict(check_bundle, snapshot["points"], snapshot["init_time"])
    if len(predictions) != 96 or any(not 0 <= p["normalized_power"] <= 1 for p in predictions):
        raise ValueError("CPU serving fixture prediction failed")
    return {"model_sha256": digest, "cpu_fixture_predictions": len(predictions), "bundle_kind": bundle["kind"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads((args.dataset_dir / "manifest.json").read_text(encoding="utf-8"))
    frame = pd.read_csv(args.dataset_dir / "training_rows.csv")
    audit_dataset(args.dataset_dir, source, frame)
    train, tune, holdout, refit = subsets(frame, 0)
    if any(part.empty for part in (train, tune, holdout, refit)):
        raise ValueError("all three chronological periods need observations")
    tune_scores = score_candidates(train, tune)
    winner = min(tune_scores, key=lambda n: (tune_scores[n]["overall"]["mae"], n != "power_curve_gfs"))
    # Frozen selection: January is evaluated exactly once after the December decision.
    before_january = pd.concat([train, tune], ignore_index=True)
    holdout_scores = score_candidates(before_january, holdout)
    previous = pd.read_csv(ROOT / "data/gfs-training/mvp-2025-08-to-2026-01/training_rows.csv")
    direct_comparison = previous_curve_comparison(previous, before_january, holdout)
    delay = {}
    for hours in (0, 6, 24, 48, 72):
        a, b, c, d = subsets(frame, hours)
        delay[str(hours)] = {"train_rows": len(a), "tune_rows": len(b),
                             "holdout_rows": len(c), "refit_rows": len(d)}
    key_hash = hashlib.sha256(strict_json(list(zip(holdout.origin_time, holdout.target_time,
                                                   holdout.turbine_id, strict=True))).encode()).hexdigest()
    result = {"metrics_kind": "retrospective_archived_gfs_january_final_holdout",
              "selection_period": "2025-12", "holdout_period": "2026-01",
              "selection_rule": "lowest December MAE; power curve wins exact tie",
              "selected_candidate": winner, "holdout_key_sha256": key_hash,
              "split": {"train_rows": len(train), "train_origins": train.origin_time.nunique(),
                        "tune_rows": len(tune), "tune_origins": tune.origin_time.nunique(),
                        "holdout_rows": len(holdout), "holdout_origins": holdout.origin_time.nunique(),
                        "refit_rows": len(refit), "refit_origins": refit.origin_time.nunique(),
                        "train_max_label_available_at": str(pd.to_datetime(train.label_available_at, utc=True).max()),
                        "tune_max_label_available_at": str(pd.to_datetime(tune.label_available_at, utc=True).max())},
              "december_tune": tune_scores, "january_holdout": holdout_scores,
              "previous_curve_same_rows": direct_comparison,
              "scada_delay_sensitivity_hours": delay,
              "source_origins_requested": source["origins_requested"],
              "source_origins_complete": source["origins_complete"],
              "source_origins_failed": source["origins_failed"],
              "source_missing_power_labels": source["missing_power_labels"]}
    verify = write_bundle(args.out_dir, winner, refit, args.dataset_dir, source, result)
    print(strict_json({"selected": winner,
                       "january_baseline_mae": holdout_scores["power_curve_gfs"]["overall"]["mae"],
                       "january_selected_mae": holdout_scores[winner]["overall"]["mae"], **verify}))


if __name__ == "__main__":
    main()

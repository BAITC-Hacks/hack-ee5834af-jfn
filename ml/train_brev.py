"""Hourly SCADA power model. Observed weather is not an archived forecast.

python train_brev.py --config config.json --turbine-1 data/raw/turbine_1.csv \
    --turbine-2 data/raw/turbine_2.csv --out artifacts
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
import sklearn
from sklearn.metrics import mean_absolute_error, mean_squared_error

SOURCE = {
    "Статистическое время": "timestamp",
    "Средняя скорость ветра(m/s)": "wind_speed",
    "Нормализованная активная мощность": "power",
    "Средняя температура окружающей среды(°C)": "temperature",
}
VALUES = ["wind_speed", "temperature", "power"]
FEATURES = ["wind_speed", "temperature", "wind_sq", "wind_cu", "hour", "month", "day_of_year", "turbine_id"]
TRAIN_END = pd.Timestamp("2025-11-01 00:00:00")
VALID_END = pd.Timestamp("2026-02-01 00:00:00")
MINUTES = {0, 10, 20, 30, 40, 50}
SEED = 42


def strict_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def localize(times: pd.Series | pd.Timestamp, timezone: str):
    # Ambiguous civil hours must be resolved from SCADA metadata, not guessed.
    if isinstance(times, pd.Series):
        return times.dt.tz_localize(timezone, ambiguous="raise", nonexistent="raise")
    return times.tz_localize(timezone, ambiguous="raise", nonexistent="raise")


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = ["timestamp_format", "source_timezone", "first_forecast_origin", "scada_delay_hours", "wind_speed_max_mps"]
    missing = [key for key in required if config.get(key) is None]
    if missing:
        raise ValueError(f"Verified config values required before training: {missing}")
    if config["timestamp_format"] != "%Y-%m-%d %H:%M:%S":
        raise ValueError("This CSV requires timestamp_format '%Y-%m-%d %H:%M:%S'")
    try:
        ZoneInfo(config["source_timezone"])
    except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
        raise ValueError("source_timezone must be a verified IANA timezone") from exc
    for key in ("scada_delay_hours", "wind_speed_max_mps"):
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{key} must be finite numeric")
    if config["scada_delay_hours"] < 0 or config["wind_speed_max_mps"] <= 0:
        raise ValueError("Delay must be nonnegative; maximum wind must be positive")
    origin = pd.to_datetime(config["first_forecast_origin"], format=config["timestamp_format"], exact=True, errors="raise")
    localize(origin, config["source_timezone"])
    config["first_forecast_origin"] = origin.strftime("%Y-%m-%d %H:%M:%S")
    return config


def parse_time(values: pd.Series, fmt: str) -> pd.Series:
    parsed = pd.to_datetime(values.astype("string"), format=fmt, exact=True, errors="coerce")
    if parsed.isna().any():
        bad = values.loc[parsed.isna()].head(5).tolist()
        raise ValueError(f"Invalid/missing timestamp format in {int(parsed.isna().sum())} rows: {bad}")
    return parsed


def prepare(path: Path, turbine_id: int, config: dict) -> tuple[pd.DataFrame, dict]:
    raw = pd.read_csv(path, encoding="utf-8-sig")
    expected = {"ID", *SOURCE}
    if set(raw.columns) != expected or len(raw.columns) != len(expected):
        raise ValueError(f"Unexpected CSV columns in {path.name}: {list(raw.columns)}")
    raw = raw.rename(columns=SOURCE)
    raw["timestamp"] = parse_time(raw.timestamp, config["timestamp_format"])
    # Source-clock timestamps remain naive throughout aggregation and splitting.
    # The 2024 Kazakhstan clock rollback creates an ambiguous local hour; its
    # UTC position cannot be inferred from this CSV without an offset/fold bit.
    ambiguous_count = int(raw.timestamp.dt.tz_localize(
        config["source_timezone"], ambiguous="NaT", nonexistent="NaT").isna().sum())
    for name in VALUES:
        raw[name] = pd.to_numeric(raw[name], errors="coerce")
    raw = raw.replace([np.inf, -np.inf], np.nan)
    report = {"file": path.name, "sha256": sha256(path), "raw_rows": int(len(raw)),
              "source_columns": sorted(expected), "source_timezone": config["source_timezone"],
              "ambiguous_or_nonexistent_local_timestamp_rows": ambiguous_count,
              "timestamp_policy": "aggregate and split in source-clock time; ambiguous historical UTC instants are not inferred",
              "missing_by_column": raw[["timestamp", *VALUES]].isna().sum().astype(int).to_dict()}

    duplicate = raw.loc[raw.duplicated("timestamp", keep=False)]
    groups = duplicate.groupby("timestamp", sort=False)
    conflicts = {time for time, group in groups if group[VALUES].drop_duplicates().shape[0] > 1}
    report["duplicate_timestamp_groups"] = int(duplicate.timestamp.nunique())
    report["conflicting_duplicate_groups"] = len(conflicts)
    report["identical_duplicate_groups"] = report["duplicate_timestamp_groups"] - len(conflicts)
    report["conflicting_duplicate_rows_excluded"] = int(raw.timestamp.isin(conflicts).sum())

    missing = raw[VALUES].isna().any(axis=1)
    wind_bad = raw.wind_speed.lt(0) | raw.wind_speed.gt(config["wind_speed_max_mps"])
    power_bad = raw.power.lt(0) | raw.power.gt(1)
    temperature_bad = raw.temperature.lt(-60) | raw.temperature.gt(60)
    grid_bad = ~raw.timestamp.dt.minute.isin(MINUTES) | raw.timestamp.dt.second.ne(0) | raw.timestamp.dt.microsecond.ne(0)
    rejected = missing | wind_bad | power_bad | temperature_bad | grid_bad | raw.timestamp.isin(conflicts)
    report["excluded_row_reasons"] = {"missing_measurement": int(missing.sum()),
                                      "wind_out_of_range": int(wind_bad.sum()),
                                      "power_out_of_range": int(power_bad.sum()),
                                      "temperature_out_of_range": int(temperature_bad.sum()),
                                      "off_grid": int(grid_bad.sum())}
    report["unique_rejected_rows"] = int(rejected.sum())
    report["off_grid_hours"] = int(raw.loc[grid_bad, "timestamp"].dt.floor("h").nunique())
    tainted_hours = set(raw.loc[rejected, "timestamp"].dt.floor("h"))
    clean = raw.loc[~rejected].drop_duplicates("timestamp").sort_values("timestamp")
    report["identical_duplicate_extra_rows_removed"] = int((~rejected).sum() - len(clean))
    report["first_timestamp"] = raw.timestamp.min().isoformat() if len(raw) else None
    report["last_timestamp"] = raw.timestamp.max().isoformat() if len(raw) else None
    gaps = clean.timestamp.diff().dropna()
    report["gaps_over_10_minutes"] = int(gaps.gt(pd.Timedelta(minutes=10)).sum())
    report["longest_gap_hours"] = float(gaps.max() / pd.Timedelta(hours=1)) if len(gaps) else None
    report["wind_speed_max_observed_mps"] = float(raw.wind_speed.max()) if raw.wind_speed.notna().any() else None
    if clean.empty:
        report.update({"hour_slots": 0, "incomplete_or_tainted_hours": 0, "retained_hours": 0})
        return pd.DataFrame(columns=["timestamp", *VALUES, "turbine_id"]), report
    clean = clean.set_index("timestamp")
    hourly = clean[VALUES].resample("h").mean()
    minute_sets = clean.groupby(pd.Grouper(freq="h")).apply(lambda g: set(g.index.minute) if len(g) else set())
    complete = minute_sets.reindex(hourly.index, fill_value=set()).map(lambda minutes: minutes == MINUTES).to_numpy()
    tainted = hourly.index.isin(tainted_hours)
    keep = complete & ~tainted
    report["hour_slots"] = int(len(hourly))
    report["tainted_hours"] = int(tainted.sum())
    report["incomplete_or_tainted_hours"] = int((~keep).sum())
    result = hourly.loc[keep].reset_index()
    result["turbine_id"] = turbine_id
    report["retained_hours"] = int(len(result))
    return result, report


def make_features(data: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "wind_speed", "temperature", "turbine_id"}
    if not required.issubset(data.columns):
        raise ValueError(f"Prediction columns missing: {sorted(required - set(data.columns))}")
    frame = data.copy()
    frame["timestamp"] = pd.to_datetime(frame.timestamp, errors="raise")
    if frame.timestamp.dt.tz is not None:
        raise ValueError("Prediction timestamps must use naive source-clock values")
    for col in ("wind_speed", "temperature", "turbine_id"):
        frame[col] = pd.to_numeric(frame[col], errors="raise")
    if not np.isfinite(frame[["wind_speed", "temperature", "turbine_id"]].to_numpy()).all():
        raise ValueError("Non-finite prediction features")
    frame["wind_sq"] = frame.wind_speed ** 2
    frame["wind_cu"] = frame.wind_speed ** 3
    frame["hour"] = frame.timestamp.dt.hour
    frame["month"] = frame.timestamp.dt.month
    frame["day_of_year"] = frame.timestamp.dt.dayofyear
    features = frame[FEATURES].astype("float64")
    if not np.isfinite(features.to_numpy()).all():
        raise ValueError("Derived prediction features are non-finite")
    return features


def fit_curve(train: pd.DataFrame) -> dict:
    if train.empty:
        raise ValueError("Power-curve train is empty")
    bins = np.floor(train.wind_speed.to_numpy() * 2).astype(int)
    mean = float(train.power.mean())
    grouped = train.groupby(bins).power.agg(["sum", "count"])
    return {"global_mean": mean, "bins": {int(k): float((row["sum"] + 20 * mean) / (row["count"] + 20)) for k, row in grouped.iterrows()}}


def predict_curve(curve: dict, data: pd.DataFrame) -> np.ndarray:
    bins = np.floor(data.wind_speed.to_numpy() * 2).astype(int)
    return np.array([curve["bins"].get(int(b), curve["global_mean"]) for b in bins], dtype=float)


def predict_bundle(bundle: dict, data: pd.DataFrame) -> np.ndarray:
    features = make_features(data)
    if list(features.columns) != bundle["features"]:
        raise ValueError("Saved feature order differs from serving feature order")
    if not data.turbine_id.isin([1, 2]).all():
        raise ValueError("Unknown turbine_id")
    kind = bundle["kind"]
    if kind == "pooled_lgbm":
        predicted = bundle["models"]["pooled"].predict(features)
    else:
        predicted = np.empty(len(data), dtype=float)
        for turbine_id in (1, 2):
            mask = data.turbine_id.eq(turbine_id).to_numpy()
            if not mask.any():
                continue
            if kind == "separate_lgbm":
                predicted[mask] = bundle["models"][str(turbine_id)].predict(features.iloc[np.flatnonzero(mask)])
            elif kind == "power_curve":
                predicted[mask] = predict_curve(bundle["curves"][str(turbine_id)], data.iloc[np.flatnonzero(mask)])
            else:
                raise ValueError(f"Unknown kind: {kind}")
    predicted = np.asarray(predicted, dtype=float)
    if not np.isfinite(predicted).all():
        raise ValueError("Model produced non-finite predictions")
    return np.clip(predicted, 0, 1)


def score(actual: np.ndarray, predicted: np.ndarray) -> dict:
    actual = np.asarray(actual, dtype=float)
    predicted = np.clip(np.asarray(predicted, dtype=float), 0, 1)
    if not len(actual) or len(actual) != len(predicted):
        raise ValueError("Empty or misaligned metric arrays")
    if not np.isfinite(actual).all() or not np.isfinite(predicted).all():
        raise ValueError("Non-finite metric input")
    r2, reason = None, None
    if len(actual) < 2:
        reason = "R² requires at least two rows"
    elif np.all(actual == actual[0]):
        reason = "R² is undefined for a constant target"
    else:
        r2 = float(1 - np.sum((actual - predicted) ** 2) / np.sum((actual - actual.mean()) ** 2))
    return {"n": int(len(actual)), "mae": float(mean_absolute_error(actual, predicted)),
            "rmse": float(np.sqrt(mean_squared_error(actual, predicted))), "r2": r2,
            "r2_unavailable_reason": reason, "bias": float(np.mean(predicted - actual))}


def split_data(data: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    origin = pd.Timestamp(config["first_forecast_origin"])
    availability = pd.Timedelta(hours=1 + config["scada_delay_hours"])
    train = data.loc[data.timestamp.lt(TRAIN_END) & data.timestamp.add(availability).le(TRAIN_END)].copy()
    valid = data.loc[data.timestamp.ge(TRAIN_END) & data.timestamp.lt(VALID_END)].copy()
    selection = valid.loc[valid.timestamp.add(availability).le(origin)].copy()
    final = data.loc[data.timestamp.add(availability).le(origin)].copy()
    for name, subset in (("train", train), ("validation", valid), ("selection", selection), ("final_train", final)):
        minimum = 2 if name in ("train", "final_train") else 1
        insufficient = {i: int(subset.turbine_id.eq(i).sum()) for i in (1, 2)
                        if subset.turbine_id.eq(i).sum() < minimum}
        if insufficient:
            raise ValueError(f"{name} needs at least {minimum} rows per turbine; observed {insufficient}")
    sort = lambda frame: frame.sort_values(["turbine_id", "timestamp"]).reset_index(drop=True)
    train, valid, selection, final = sort(train), sort(valid), sort(selection), sort(final)
    meta = {"forecast_origin_source_clock": str(origin), "scada_delay_hours": config["scada_delay_hours"],
             "label_availability_rule": "hour start + 1 hour + delay <= cutoff",
             "selection_latest_hour": str(selection.timestamp.max()), "final_latest_hour": str(final.timestamp.max()),
             "validation_rows": {f"turbine_{i}": int(valid.turbine_id.eq(i).sum()) for i in (1, 2)},
             "selection_rows": {f"turbine_{i}": int(selection.turbine_id.eq(i).sum()) for i in (1, 2)}}
    return train, valid, selection, final, meta


def new_model() -> lgb.LGBMRegressor:
    return lgb.LGBMRegressor(n_estimators=500, learning_rate=0.035, num_leaves=31,
                             min_child_samples=100, subsample=0.9, colsample_bytree=0.9,
                             reg_lambda=2.0, random_state=SEED, n_jobs=2, verbosity=-1)


def train_candidates(train: pd.DataFrame) -> dict:
    curves = {str(i): fit_curve(train.loc[train.turbine_id.eq(i)]) for i in (1, 2)}
    pooled = new_model()
    pooled.fit(make_features(train), train.power)
    separate = {}
    for i in (1, 2):
        subset = train.loc[train.turbine_id.eq(i)]
        model = new_model()
        model.fit(make_features(subset), subset.power)
        separate[str(i)] = model
    return {"power_curve": {"curves": curves}, "pooled_lgbm": {"models": {"pooled": pooled}},
            "separate_lgbm": {"models": separate}}


def compare_candidates(candidates: dict, valid: pd.DataFrame) -> tuple[dict, str]:
    keys = list(zip(valid.turbine_id.astype(int).tolist(), valid.timestamp.astype(str).tolist()))
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate validation turbine/timestamp keys")
    metrics = {}
    order = ("power_curve", "pooled_lgbm", "separate_lgbm")
    key_hash = hashlib.sha256(strict_json(keys).encode()).hexdigest()
    for kind in order:
        bundle = {"kind": kind, "features": FEATURES, **candidates[kind]}
        predicted = predict_bundle(bundle, valid)
        if len(predicted) != len(keys):
            raise ValueError(f"{kind} did not predict all validation rows")
        metrics[kind] = {"validation_key_sha256": key_hash}
        for i in (1, 2):
            mask = valid.turbine_id.eq(i).to_numpy()
            metrics[kind][f"turbine_{i}"] = score(valid.power.to_numpy()[mask], predicted[mask])
        metrics[kind]["overall"] = score(valid.power.to_numpy(), predicted)
        metrics[kind]["macro_mae"] = float(np.mean([metrics[kind][f"turbine_{i}"]["mae"] for i in (1, 2)]))
    winner = min(order, key=lambda kind: (metrics[kind]["macro_mae"], order.index(kind)))
    return metrics, winner


def fit_final(kind: str, final: pd.DataFrame) -> dict:
    if kind == "power_curve":
        content = {"curves": {str(i): fit_curve(final.loc[final.turbine_id.eq(i)]) for i in (1, 2)}}
    elif kind == "pooled_lgbm":
        model = new_model()
        model.fit(make_features(final), final.power)
        content = {"models": {"pooled": model}}
    else:
        models = {}
        for i in (1, 2):
            subset = final.loc[final.turbine_id.eq(i)]
            model = new_model()
            model.fit(make_features(subset), subset.power)
            models[str(i)] = model
        content = {"models": models}
    return {"kind": kind, "features": FEATURES, **content}


def save_and_check(bundle: dict, sample: pd.DataFrame, out: Path) -> None:
    if sample.turbine_id.nunique() != 2:
        raise ValueError("CPU round-trip sample must include both turbines")
    before = predict_bundle(bundle, sample)
    path = out / "best_model.joblib"
    joblib.dump(bundle, path)
    loaded = joblib.load(path)
    after = predict_bundle(loaded, sample)
    if not np.allclose(before, after, atol=1e-8, rtol=0):
        raise RuntimeError("CPU model round-trip changed predictions")
    if bundle["kind"] == "pooled_lgbm":
        bundle["models"]["pooled"].booster_.save_model(str(out / "model.txt"))
    elif bundle["kind"] == "separate_lgbm":
        for i in (1, 2):
            bundle["models"][str(i)].booster_.save_model(str(out / f"model_turbine_{i}.txt"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--turbine-1", type=Path, required=True)
    parser.add_argument("--turbine-2", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    args.out.mkdir(parents=True, exist_ok=True)
    frames, profiles = [], {}
    for i, path in ((1, args.turbine_1), (2, args.turbine_2)):
        frame, profile = prepare(path, i, config)
        frames.append(frame)
        profiles[f"turbine_{i}"] = profile
    (args.out / "data_analysis.json").write_text(strict_json(profiles), encoding="utf-8")
    data = pd.concat(frames, ignore_index=True)
    train, valid, selection, final, split_meta = split_data(data, config)
    candidates = train_candidates(train)
    selection_metrics, winner = compare_candidates(candidates, selection)
    metrics, _ = compare_candidates(candidates, valid)
    bundle = fit_final(winner, final)
    bundle["config"] = config
    save_and_check(bundle, selection.groupby("turbine_id", sort=True).head(50), args.out)
    result = {"source": profiles, "train": [str(train.timestamp.min()), str(train.timestamp.max())],
              "validation": [str(valid.timestamp.min()), str(valid.timestamp.max())],
              "selection": [str(selection.timestamp.min()), str(selection.timestamp.max())], "split": split_meta,
              "selection_rule": "lowest mean turbine MAE on labels available by forecast origin; tie priority power_curve, pooled_lgbm, separate_lgbm",
              "winner": winner, "models": metrics, "selection_models": selection_metrics, "features": FEATURES,
              "limitations": ["Actual historical wind/temperature: conditional estimate, not an archived weather forecast backtest.",
                              "Source timezone and forecast origin require confirmation from the data owner."],
              "versions": {"python": platform.python_version(), "pandas": pd.__version__,
                           "scikit_learn": sklearn.__version__, "lightgbm": lgb.__version__}}
    (args.out / "metrics.json").write_text(strict_json(result), encoding="utf-8")
    print(strict_json({"winner": winner, "metrics": metrics, "split": split_meta}))


if __name__ == "__main__":
    main()

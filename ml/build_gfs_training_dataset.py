"""Reproduce point-in-time GFS -> hourly SCADA power training rows.

Run from the repository root. A failed origin stays visible in manifest.json.
The snapshot cache contains original source URL, Last-Modified and byte hashes.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.forecasting.gfs_features import FEATURE_ORDER, TIMEZONE, make_gfs_features
from backend.replay import ReplayContext, parse_timestamp, validate_weather_snapshot
from backend.weather.gfs import build_snapshot
from ml.train_brev import load_config, prepare, sha256, strict_json


def origins(start: datetime, end: datetime, step_days: int) -> list[datetime]:
    if start >= end or step_days < 1 or any((start.minute, start.second, start.microsecond,
                                              end.minute, end.second, end.microsecond)):
        raise ValueError("start/end must be aligned hours; end must follow start; step_days >= 1")
    result = []
    current = start
    while current < end:
        result.append(current)
        current += timedelta(days=step_days)
    return result


def hourly_labels(turbine_1: Path, turbine_2: Path, config_path: Path):
    config = load_config(config_path)
    zone = ZoneInfo(config["source_timezone"])
    lookup = {}
    analysis = {}
    for turbine_id, path in ((1, turbine_1), (2, turbine_2)):
        hours, report = prepare(path, turbine_id, config)
        analysis[f"T{turbine_id}"] = report
        for row in hours.itertuples(index=False):
            local = pd.Timestamp(row.timestamp).to_pydatetime().replace(tzinfo=zone)
            # For ambiguous 2024 civil hours the source CSV lacks a fold bit.
            # The chosen dataset range is later; reject any ambiguous labels.
            if local.replace(fold=0).utcoffset() != local.replace(fold=1).utcoffset():
                continue
            lookup[(f"T{turbine_id}", local.astimezone(timezone.utc))] = float(row.power)
    return lookup, analysis, config


def rows_from_snapshot(snapshot: dict, origin: datetime, labels: dict,
                       scada_delay_hours: float = 0) -> tuple[list[dict], int]:
    validate_weather_snapshot(snapshot, ReplayContext(origin, 48))
    result, missing = [], 0
    for point in snapshot["points"]:
        target = parse_timestamp(point["target_start"])
        key = point["turbine_id"], target
        power = labels.get(key)
        if power is None:
            missing += 1
            continue
        features = make_gfs_features(point, origin, snapshot["init_time"])
        result.append({
            "origin_time": origin.isoformat().replace("+00:00", "Z"),
            "target_time": point["target_start"],
            "target_end": point["target_end"],
            "label_available_at": (parse_timestamp(point["target_end"]) + timedelta(hours=scada_delay_hours)).isoformat().replace("+00:00", "Z"),
            "gfs_init": snapshot["init_time"],
            "gfs_available_at": snapshot["available_at"],
            **features,
            "target_power": power,
        })
    return result, missing


def build(start: datetime, end: datetime, step_days: int, turbine_1: Path,
          turbine_2: Path, config_path: Path, cache_dir: Path, out_dir: Path,
          workers: int = 3, explicit_origins: list[datetime] | None = None) -> dict:
    selected = explicit_origins if explicit_origins is not None else origins(start, end, step_days)
    if not selected or selected != sorted(set(selected)):
        raise ValueError("forecast origins must be nonempty, unique and sorted")
    if any(origin < start or origin >= end or origin.hour != start.hour or
           origin.minute or origin.second or origin.microsecond for origin in selected):
        raise ValueError("explicit origins must lie in range and follow the configured UTC hour")
    labels, analysis, config = hourly_labels(turbine_1, turbine_2, config_path)
    if config["source_timezone"] != TIMEZONE:
        raise ValueError("SCADA timezone differs from shared GFS feature policy")
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    statuses = {}
    all_rows = []

    def fetch(origin: datetime):
        return build_snapshot(origin, 48, cache_dir)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(fetch, origin): origin for origin in selected}
        for future in as_completed(pending):
            origin = pending[future]
            key = origin.isoformat().replace("+00:00", "Z")
            try:
                snapshot = future.result()
                rows, missing = rows_from_snapshot(snapshot, origin, labels, config["scada_delay_hours"])
                snapshot_path = cache_dir / f"noaa-gfs-{origin:%Y%m%dT%H%M%SZ}-h48.json"
                archive_dir = out_dir / "snapshots"
                archive_dir.mkdir(exist_ok=True)
                archived = archive_dir / snapshot_path.name
                shutil.copyfile(snapshot_path, archived)
                shutil.copyfile(snapshot_path.with_suffix(".json.sha256"), archived.with_suffix(".json.sha256"))
                statuses[key] = {
                    "status": "complete", "gfs_init": snapshot["init_time"],
                    "gfs_available_at": snapshot["available_at"],
                    "gfs_cycle": snapshot["init_time"],
                    "snapshot_sha256": sha256(snapshot_path),
                    "snapshot_relpath": f"snapshots/{snapshot_path.name}",
                    "source_objects": len(snapshot["objects"]),
                    "rows": len(rows), "missing_power_labels": missing,
                }
                all_rows.extend(rows)
            except Exception as exc:
                statuses[key] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            print(f"{key}: {statuses[key]['status']} rows={statuses[key].get('rows', 0)}", flush=True)
            # Preserve progress and failure diagnostics after each expensive origin.
            (out_dir / "progress.json").write_bytes((strict_json(statuses) + "\n").encode("utf-8"))

    frame = pd.DataFrame(all_rows)
    if frame.empty:
        raise RuntimeError("No usable archived-GFS/SCADA joins; inspect progress.json")
    frame = frame.sort_values(["origin_time", "target_time", "turbine_id"]).reset_index(drop=True)
    dataset_path = out_dir / "training_rows.csv"
    frame.to_csv(dataset_path, index=False, float_format="%.17g", lineterminator="\n")
    successful = [s for s in statuses.values() if s["status"] == "complete"]
    manifest = {
        "dataset_kind": "archived_noaa_gfs_forecast_to_hourly_scada_power_v1",
        "origin_policy": {"start_inclusive": start.isoformat(), "end_exclusive": end.isoformat(),
                          "step_days": (step_days if explicit_origins is None else None),
                          "hour_utc": start.hour, "horizon_hours": 48,
                          "selection": ("regular_interval" if explicit_origins is None else "explicit_historical_subset"),
                          "selected_origins": [o.isoformat().replace("+00:00", "Z") for o in selected]},
        "weather_provider": "noaa_gfs", "availability_basis": "s3_last_modified",
        "weather_semantics": "instantaneous forecast at target_start",
        "source_timezone": TIMEZONE, "target_aggregation": "mean of six valid 10-minute power readings",
        "feature_order": list(FEATURE_ORDER),
        "scada_csv_sha256": {"T1": sha256(turbine_1), "T2": sha256(turbine_2)},
        "scada_assumptions": {"scada_delay_hours": config["scada_delay_hours"],
                              "wind_speed_max_mps": config["wind_speed_max_mps"]},
        "scada_analysis": analysis, "origins_requested": len(selected),
        "origins_complete": len(successful), "origins_failed": len(selected) - len(successful),
        "gfs_cycles": sorted(set(s["gfs_cycle"] for s in successful)),
        "rows": len(frame), "missing_power_labels": sum(s["missing_power_labels"] for s in successful),
        "dataset_sha256": sha256(dataset_path), "origins": dict(sorted(statuses.items())),
    }
    (out_dir / "manifest.json").write_bytes((strict_json(manifest) + "\n").encode("utf-8"))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="Inclusive forecast origin, offset-aware ISO 8601")
    parser.add_argument("--end", required=True, help="Exclusive forecast origin, offset-aware ISO 8601")
    parser.add_argument("--step-days", type=int, default=7)
    parser.add_argument("--turbine-1", type=Path, default=ROOT / "data/training/turbine_1.csv")
    parser.add_argument("--turbine-2", type=Path, default=ROOT / "data/training/turbine_2.csv")
    parser.add_argument("--config", type=Path, default=ROOT / "ml/config.brev-assumptions.json")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".cache/weather")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--origins-file", type=Path,
                        help="Optional newline-separated UTC origins; overrides --step-days")
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        parser.error("--workers must be in [1, 8]")
    explicit = None
    if args.origins_file:
        explicit = [parse_timestamp(line) for line in args.origins_file.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
    manifest = build(parse_timestamp(args.start), parse_timestamp(args.end), args.step_days,
                     args.turbine_1, args.turbine_2, args.config, args.cache_dir,
                     args.out_dir, args.workers, explicit)
    if args.origins_file:
        manifest["origin_list_sha256"] = sha256(args.origins_file)
        (args.out_dir / "manifest.json").write_bytes((strict_json(manifest) + "\n").encode("utf-8"))
    print(f"Complete: {manifest['origins_complete']}/{manifest['origins_requested']} origins; {manifest['rows']} rows")


if __name__ == "__main__":
    main()

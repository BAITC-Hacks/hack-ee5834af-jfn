#!/usr/bin/env python3
"""Build and audit point-in-time weather inputs for a replay origin."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from backend.replay import (
    ReplayContext,
    parse_timestamp,
    select_scada,
    validate_artifact_cutoffs,
    validate_weather_snapshot,
)


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_scada(
    path: Path,
    context: ReplayContext,
    latency_minutes: int,
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    if latency_minutes < 0:
        raise ValueError("SCADA latency must be non-negative")
    with path.open("r", encoding="utf-8", newline="") as handle:
        records = list(csv.DictReader(handle))
    if not records:
        raise ValueError("SCADA CSV is empty")
    prepared = []
    for index, record in enumerate(records):
        if not record.get("interval_end"):
            raise ValueError(f"SCADA row {index + 2} has no interval_end")
        interval_end = parse_timestamp(record["interval_end"])
        prepared.append(
            {
                **record,
                "interval_end": interval_end.isoformat(),
                "available_at": (
                    interval_end + timedelta(minutes=latency_minutes)
                ).isoformat(),
            }
        )
    selected = select_scada(prepared, context)
    latest = max(
        (parse_timestamp(row["interval_end"]) for row in selected), default=None
    )
    audit = {
        "status": "VERIFIED" if selected else "VERIFIED_EMPTY",
        "source_sha256": _sha256(path),
        "timezone_policy": "offset_required_in_each_interval_end",
        "interval_semantics": "right_edge_of_completed_hour",
        "latency_minutes": latency_minutes,
        "selected_rows": len(selected),
        "latest_interval_end": latest.isoformat() if latest else None,
    }
    return selected, audit


def run_replay(args: argparse.Namespace) -> dict[str, Any]:
    context = ReplayContext(parse_timestamp(args.as_of), args.horizon)
    warnings = []

    if args.snapshot:
        snapshot_path = Path(args.snapshot)
        snapshot = _read_json(snapshot_path)
        weather_source = {
            "mode": "offline_snapshot",
            "path": str(snapshot_path),
            "sha256": _sha256(snapshot_path),
        }
    else:
        from backend.weather.gfs import build_snapshot

        snapshot = build_snapshot(context.as_of, context.horizon_hours, args.cache_dir)
        weather_source = {"mode": "live_noaa_gfs", "cache_dir": str(args.cache_dir)}
    validate_weather_snapshot(snapshot, context)

    if args.scada_csv:
        _, scada_audit = _load_scada(
            Path(args.scada_csv), context, args.scada_latency_minutes
        )
    else:
        scada_audit = {
            "status": "NOT_VERIFIED",
            "latest_interval_end": None,
        }
        warnings.append("SCADA was not supplied; its status is NOT_VERIFIED.")

    if args.model_metadata:
        model_path = Path(args.model_metadata)
        model_metadata = _read_json(model_path)
        if not isinstance(model_metadata, Mapping):
            raise ValueError("model metadata must be a JSON object")
        validate_artifact_cutoffs(model_metadata, context)
        model_audit = {
            "status": "VERIFIED_CUTOFFS_ONLY",
            "source_sha256": _sha256(model_path),
            "model_id": model_metadata.get("model_id"),
            "training_cutoff": parse_timestamp(
                model_metadata["training_cutoff"]
            ).isoformat(),
        }
    else:
        model_audit = {"status": "NOT_VERIFIED", "model_id": None}
        warnings.append("Model metadata was not supplied; model status is NOT_VERIFIED.")

    return {
        "stage": "weather_inputs_ready",
        "as_of": context.as_of.isoformat(),
        "executed_at": parse_timestamp(args.executed_at).isoformat(),
        "horizon_hours": context.horizon_hours,
        "forecast_power": None,
        "weather": {
            "provider": snapshot["provider"],
            "init_time": parse_timestamp(snapshot["init_time"]).isoformat(),
            "available_at": parse_timestamp(snapshot["available_at"]).isoformat(),
            "availability_basis": snapshot["availability_basis"],
            "point_count": len(snapshot["points"]),
            "objects": [
                {
                    "url": obj["url"],
                    "index_url": obj.get("index_url"),
                    "sha256": obj["sha256"],
                    "sha256_scope": obj.get("sha256_scope"),
                    "index_sha256": obj.get("index_sha256"),
                    "available_at": parse_timestamp(obj["available_at"]).isoformat(),
                    "index_available_at": parse_timestamp(
                        obj["index_available_at"]
                    ).isoformat(),
                }
                for obj in snapshot["objects"]
            ],
            "source": weather_source,
        },
        "scada": scada_audit,
        "model": model_audit,
        "warnings": warnings,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare verified historical weather inputs; no power model is run."
    )
    parser.add_argument("--as-of", required=True, help="Hourly ISO 8601 origin with offset")
    parser.add_argument("--horizon", required=True, type=int, choices=(24, 48))
    parser.add_argument("--snapshot", help="Cached JSON weather snapshot for offline replay")
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/weather"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--scada-csv", help="CSV with offset-aware interval_end values")
    parser.add_argument("--scada-latency-minutes", type=int)
    parser.add_argument("--model-metadata", help="Optional JSON artifact metadata")
    parser.add_argument("--executed-at", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if bool(args.scada_csv) != (args.scada_latency_minutes is not None):
        parser.error("--scada-csv and --scada-latency-minutes must be supplied together")
    if args.executed_at is None:
        from datetime import datetime, timezone

        args.executed_at = datetime.now(timezone.utc).isoformat()
    try:
        result = run_replay(args)
    except (
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        parser.exit(2, f"replay validation failed: {exc}\n")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "weather-inputs-audit.json"
    with output.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build a daily UTC weather-snapshot batch for an explicit date range."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.replay import ReplayContext, validate_weather_snapshot
from backend.weather.gfs import build_snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="First UTC date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Last UTC date, YYYY-MM-DD")
    parser.add_argument("--origin-hour", required=True, type=int, choices=range(24))
    parser.add_argument("--horizon", type=int, choices=(24, 48), default=48)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(
        hour=args.origin_hour, tzinfo=timezone.utc
    )
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(
        hour=args.origin_hour, tzinfo=timezone.utc
    )
    if end < start:
        parser.error("--end must be on or after --start")

    rows = []
    origin = start
    while origin <= end:
        context = ReplayContext(origin, args.horizon)
        snapshot = build_snapshot(origin, args.horizon, args.cache_dir)
        validate_weather_snapshot(snapshot, context)
        rows.append(
            {
                "as_of": origin.isoformat(),
                "horizon_hours": args.horizon,
                "init_time": snapshot["init_time"],
                "available_at": snapshot["available_at"],
                "objects": len(snapshot["objects"]),
                "points": len(snapshot["points"]),
            }
        )
        origin += timedelta(days=1)

    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(
            {
                "timezone": "UTC",
                "schedule_status": "explicit_demo_schedule_not_confirmed_operationally",
                "runs": rows,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(args.manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

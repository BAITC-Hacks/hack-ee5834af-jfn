"""One feature contract for archived-GFS training and CPU serving."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from backend.replay import parse_timestamp
from backend.weather.schema import WEATHER_FIELDS

TIMEZONE = "Asia/Almaty"
FEATURE_ORDER = (
    "gfs_wind_u_100m", "gfs_wind_v_100m", "gfs_wind_speed_100m",
    "gfs_wind_u_10m", "gfs_wind_v_10m", "gfs_wind_speed_10m",
    "gfs_temperature_2m", "gfs_wind_speed_100m_sq",
    "gfs_wind_speed_100m_cu", "origin_lead_hours", "gfs_lead_hours", "gfs_init_hour",
    "target_local_hour", "target_local_month", "target_local_day_of_year",
    "turbine_id",
)


def make_gfs_features(point: Mapping[str, Any], origin_time: datetime | str,
                      init_time: datetime | str) -> dict[str, float]:
    """Use only forecast fields and clocks known at the forecast origin."""
    origin = parse_timestamp(origin_time)
    init = parse_timestamp(init_time)
    target = parse_timestamp(point.get("target_start"))
    origin_lead = (target - origin).total_seconds() / 3600
    gfs_lead = (target - init).total_seconds() / 3600
    if target.minute or target.second or not 0 <= origin_lead < 48 or origin_lead != int(origin_lead):
        raise ValueError("target lead must be an integer hour in [0, 48)")
    if init > origin or gfs_lead < 0 or gfs_lead != int(gfs_lead):
        raise ValueError("GFS init/lead is incompatible with forecast origin")
    turbine = {"T1": 1.0, "T2": 2.0}.get(point.get("turbine_id"))
    if turbine is None:
        raise ValueError("unknown turbine_id")
    result = {}
    for field in WEATHER_FIELDS:
        value = point.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"invalid GFS field: {field}")
        result[f"gfs_{field}"] = float(value)
    wind = result["gfs_wind_speed_100m"]
    if wind < 0 or result["gfs_wind_speed_10m"] < 0:
        raise ValueError("negative forecast wind speed")
    local = target.astimezone(ZoneInfo(TIMEZONE))
    result.update({
        "gfs_wind_speed_100m_sq": wind * wind,
        "gfs_wind_speed_100m_cu": wind * wind * wind,
        "origin_lead_hours": origin_lead,
        "gfs_lead_hours": gfs_lead,
        "gfs_init_hour": float(init.astimezone(timezone.utc).hour),
        "target_local_hour": float(local.hour),
        "target_local_month": float(local.month),
        "target_local_day_of_year": float(local.timetuple().tm_yday),
        "turbine_id": turbine,
    })
    if tuple(result) != FEATURE_ORDER or not all(math.isfinite(x) for x in result.values()):
        raise ValueError("GFS feature schema is incompatible")
    return result

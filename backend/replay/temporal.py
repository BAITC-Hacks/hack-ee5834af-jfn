"""Strict temporal guards for reproducible, point-in-time replay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
import re
from typing import Any, Iterable, Mapping


UTC = timezone.utc


def parse_timestamp(value: Any) -> datetime:
    """Parse an offset-aware timestamp and normalize it to UTC."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("timestamp must not be empty")
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"invalid timestamp: {value!r}") from exc
    else:
        raise TypeError("timestamp must be a datetime or ISO 8601 string")

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class ReplayContext:
    """Immutable forecast origin and its hourly target intervals."""

    as_of: datetime
    horizon_hours: int = 24

    def __post_init__(self) -> None:
        normalized = parse_timestamp(self.as_of)
        if normalized.minute or normalized.second or normalized.microsecond:
            raise ValueError("as_of must be aligned to a whole hour")
        if self.horizon_hours not in (24, 48):
            raise ValueError("horizon_hours must be 24 or 48")
        object.__setattr__(self, "as_of", normalized)

    @property
    def intervals(self) -> tuple[tuple[datetime, datetime], ...]:
        hour = timedelta(hours=1)
        return tuple(
            (self.as_of + index * hour, self.as_of + (index + 1) * hour)
            for index in range(self.horizon_hours)
        )


def require_available_at(
    value: Any, context: ReplayContext, label: str = "feature"
) -> datetime:
    """Require a known availability time no later than the replay origin."""
    if value is None:
        raise ValueError(f"{label} availability is unknown")
    try:
        available_at = parse_timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} availability is invalid") from exc
    if available_at > context.as_of:
        raise ValueError(f"{label} was not available at replay origin")
    return available_at


def select_scada(
    records: Iterable[Mapping[str, Any]], context: ReplayContext
) -> tuple[dict[str, Any], ...]:
    """Copy records whose intervals and publication both precede the origin."""
    selected = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise TypeError(f"SCADA record {index} must be a mapping")
        if "interval_end" not in record or "available_at" not in record:
            raise ValueError(
                f"SCADA record {index} requires interval_end and available_at"
            )
        interval_end = parse_timestamp(record["interval_end"])
        available_at = parse_timestamp(record["available_at"])
        if interval_end <= context.as_of and available_at <= context.as_of:
            selected.append(dict(record))
    return tuple(selected)


_ARTIFACT_CUTOFFS = (
    "training_cutoff",
    "preprocessing_cutoff",
    "calibration_cutoff",
    "data_cutoff",
)


def validate_artifact_cutoffs(
    artifacts: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    context: ReplayContext,
) -> Mapping[str, Any] | Iterable[Mapping[str, Any]]:
    """Reject artifacts trained or prepared using information after ``as_of``."""
    if isinstance(artifacts, Mapping) and "training_cutoff" in artifacts:
        entries = (("artifact", artifacts),)
    elif isinstance(artifacts, Mapping):
        entries = tuple(artifacts.items())
    else:
        entries = tuple((str(index), value) for index, value in enumerate(artifacts))

    for label, artifact in entries:
        if not isinstance(artifact, Mapping):
            raise TypeError(f"artifact {label!r} must be a mapping")
        if artifact.get("training_cutoff") is None:
            raise ValueError(f"artifact {label!r} has unknown training_cutoff")
        for key in _ARTIFACT_CUTOFFS:
            if key in artifact:
                cutoff = artifact[key]
                if cutoff is None:
                    raise ValueError(f"artifact {label!r} has unknown {key}")
                if parse_timestamp(cutoff) > context.as_of:
                    raise ValueError(f"artifact {label!r} has future {key}")
    return artifacts


_WEATHER_VALUES = (
    "wind_u_100m",
    "wind_v_100m",
    "wind_speed_100m",
    "wind_u_10m",
    "wind_v_10m",
    "wind_speed_10m",
    "temperature_2m",
)


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def validate_weather_snapshot(
    snapshot: Mapping[str, Any],
    context: ReplayContext,
    turbine_ids: tuple[str, ...] = ("T1", "T2"),
) -> Mapping[str, Any]:
    """Validate availability, provenance and exact hourly forecast coverage."""
    if not isinstance(snapshot, Mapping):
        raise TypeError("weather snapshot must be a mapping")
    if snapshot.get("provider") != "noaa_gfs":
        raise ValueError("weather provider must be noaa_gfs")
    if snapshot.get("availability_basis") != "s3_last_modified":
        raise ValueError("availability_basis must be s3_last_modified")
    if snapshot.get("units") != {"wind": "m/s", "temperature": "degC"}:
        raise ValueError("weather units must be wind=m/s and temperature=degC")
    if snapshot.get("weather_semantics") != (
        "instantaneous forecast at target_start; not an hourly average"
    ):
        raise ValueError("weather semantics must identify instantaneous target_start values")
    if parse_timestamp(snapshot.get("init_time")) > context.as_of:
        raise ValueError("weather init_time is after replay origin")
    snapshot_available = require_available_at(
        snapshot.get("available_at"), context, "weather snapshot"
    )

    objects = snapshot.get("objects")
    if not isinstance(objects, list) or not objects:
        raise ValueError("weather snapshot requires at least one source object")
    source_availability = []
    for index, obj in enumerate(objects):
        if not isinstance(obj, Mapping):
            raise TypeError(f"weather object {index} must be a mapping")
        for key in ("url", "index_url"):
            if not isinstance(obj.get(key), str) or not obj[key].strip():
                raise ValueError(f"weather object {index} requires {key}")
        for key in ("sha256", "index_sha256"):
            if not isinstance(obj.get(key), str) or not re.fullmatch(
                r"[0-9a-f]{64}", obj[key]
            ):
                raise ValueError(f"weather object {index} requires lowercase SHA-256 {key}")
        source_availability.append(
            require_available_at(obj.get("available_at"), context, f"weather object {index}")
        )
        source_availability.append(
            require_available_at(
                obj.get("index_available_at"), context, f"weather object {index} index"
            )
        )
    if snapshot_available != max(source_availability):
        raise ValueError("snapshot available_at must equal latest object or index availability")

    points = snapshot.get("points")
    if not isinstance(points, list):
        raise ValueError("weather snapshot points must be a list")
    expected = {
        (turbine_id, start, end)
        for turbine_id in turbine_ids
        for start, end in context.intervals
    }
    actual = set()
    for index, point in enumerate(points):
        if not isinstance(point, Mapping):
            raise TypeError(f"weather point {index} must be a mapping")
        turbine_id = point.get("turbine_id")
        start = parse_timestamp(point.get("target_start"))
        end = parse_timestamp(point.get("target_end"))
        key = (turbine_id, start, end)
        if key in actual:
            raise ValueError(f"duplicate weather point {key!r}")
        actual.add(key)
        for field in _WEATHER_VALUES:
            value = _finite_number(point.get(field), f"weather point {index} {field}")
            if field.startswith("wind_speed_") and value < 0:
                raise ValueError(f"weather point {index} {field} must be non-negative")
    if actual != expected:
        missing = len(expected - actual)
        extra = len(actual - expected)
        raise ValueError(f"weather coverage mismatch: {missing} missing, {extra} extra")
    return snapshot

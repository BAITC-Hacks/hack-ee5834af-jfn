"""Point-in-time safe NOAA GFS archive adapter.

GFS fields are instantaneous values at ``target_start``. They are forecast
features, not averages over the following target hour.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
from pathlib import Path
import re
from threading import Lock
from typing import Any, Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .schema import TURBINES

BASE_URL = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
MAX_MESSAGE_BYTES = 8 * 1024 * 1024
FIELD_PATTERNS = {
    "wind_u_100m": ("UGRD", "100 m above ground"),
    "wind_v_100m": ("VGRD", "100 m above ground"),
    "wind_u_10m": ("UGRD", "10 m above ground"),
    "wind_v_10m": ("VGRD", "10 m above ground"),
    "temperature_2m": ("TMP", "2 m above ground"),
}
_ECCODES_LOCK = Lock()


class GfsError(RuntimeError):
    """The archive cannot produce a point-in-time-safe snapshot."""


class Http(Protocol):
    def head(self, url: str) -> dict[str, str]: ...
    def get(self, url: str, byte_range: tuple[int, int] | None = None) -> tuple[bytes, dict[str, str]]: ...


class Decoder(Protocol):
    def decode(self, message: bytes, latitude: float, longitude: float) -> dict[str, Any]: ...


class UrllibHttp:
    """Small injectable HTTP client which never downloads a GRIB without Range."""

    @staticmethod
    def _headers(response: Any) -> dict[str, str]:
        return {key.lower(): value for key, value in response.headers.items()}

    def head(self, url: str) -> dict[str, str]:
        try:
            with urlopen(Request(url, method="HEAD"), timeout=30) as response:
                return self._headers(response)
        except HTTPError as exc:
            raise GfsError(f"archive HEAD failed ({exc.code}): {url}") from exc

    def get(self, url: str, byte_range: tuple[int, int] | None = None) -> tuple[bytes, dict[str, str]]:
        headers = {}
        if byte_range is not None:
            start, end = byte_range
            if start < 0 or end < start or end - start + 1 > MAX_MESSAGE_BYTES:
                raise GfsError(f"unsafe byte range {start}-{end} for {url}")
            headers["Range"] = f"bytes={start}-{end}"
        elif not url.endswith(".idx"):
            raise GfsError(f"refusing non-range GRIB download: {url}")
        try:
            with urlopen(Request(url, headers=headers), timeout=60) as response:
                body = response.read(MAX_MESSAGE_BYTES + 1)
                if len(body) > MAX_MESSAGE_BYTES:
                    raise GfsError(f"response exceeded safety limit: {url}")
                if byte_range is not None and response.status != 206:
                    raise GfsError(f"server ignored Range request for {url}")
                return body, self._headers(response)
        except HTTPError as exc:
            raise GfsError(f"archive GET failed ({exc.code}): {url}") from exc


class EccodesDecoder:
    """Decode one GRIB message and choose the nearest model grid point."""

    def __init__(self) -> None:
        try:
            import eccodes  # type: ignore
        except ImportError as exc:
            raise GfsError("live GFS decoding requires the optional 'eccodes' package") from exc
        self.ec = eccodes
        # ecCodes has process-wide native state. Separate snapshot workers must
        # serialize decoding even when they own separate decoder instances.
        self._lock = _ECCODES_LOCK

    def decode(self, message: bytes, latitude: float, longitude: float) -> dict[str, Any]:
        ec = self.ec
        # ecCodes' Python bindings share native state; serialize the short decode.
        with self._lock:
            gid = ec.codes_new_from_message(message)
            if gid is None:
                raise GfsError("ecCodes rejected a GRIB message")
            try:
                nearest = ec.codes_grib_find_nearest(gid, latitude, longitude)[0]
                init = _grib_datetime(ec.codes_get(gid, "dataDate"), ec.codes_get(gid, "dataTime"))
                valid = _grib_datetime(ec.codes_get(gid, "validityDate"), ec.codes_get(gid, "validityTime"))
                return {
                    "value": float(nearest["value"]),
                    "units": str(ec.codes_get(gid, "units")),
                    "grid_latitude": float(nearest["lat"]),
                    "grid_longitude": float(nearest["lon"]),
                    "distance": float(nearest.get("distance", 0.0)),
                    "init_time": _iso(init),
                    "valid_time": _iso(valid),
                    "short_name": str(ec.codes_get(gid, "shortName")),
                    "level": int(ec.codes_get(gid, "level")),
                }
            finally:
                ec.codes_release(gid)


@dataclass(frozen=True)
class IndexRecord:
    number: int
    start: int
    end: int
    description: str


def _grib_datetime(date: int, time: int) -> datetime:
    return datetime.strptime(f"{int(date):08d}{int(time):04d}", "%Y%m%d%H%M").replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of must include a timezone")
    value = value.astimezone(timezone.utc)
    if value.minute or value.second or value.microsecond:
        raise ValueError("as_of must be aligned to an hour")
    return value


def _http_time(headers: dict[str, str], url: str) -> datetime:
    raw = headers.get("last-modified")
    if not raw:
        raise GfsError(f"missing Last-Modified metadata: {url}")
    return parsedate_to_datetime(raw).astimezone(timezone.utc)


def _content_length(headers: dict[str, str], url: str) -> int:
    try:
        return int(headers["content-length"])
    except (KeyError, ValueError) as exc:
        raise GfsError(f"missing Content-Length metadata: {url}") from exc


def _urls(init: datetime, lead: int) -> tuple[str, str]:
    stem = f"gfs.{init:%Y%m%d}/{init:%H}/atmos/gfs.t{init:%H}z.pgrb2.0p25.f{lead:03d}"
    url = f"{BASE_URL}/{stem}"
    return url, f"{url}.idx"


def _parse_index(text: str, object_size: int) -> dict[str, IndexRecord]:
    if object_size <= 0:
        raise GfsError("GFS object size must be positive")
    rows: list[tuple[int, int, str]] = []
    for line in text.splitlines():
        match = re.match(r"^(\d+):(\d+):(.+)$", line)
        if match:
            rows.append((int(match.group(1)), int(match.group(2)), match.group(3)))
    selected: dict[str, IndexRecord] = {}
    offsets = [row[1] for row in rows]
    if not rows or offsets != sorted(set(offsets)) or offsets[0] < 0 or offsets[-1] >= object_size:
        raise GfsError("GFS index offsets are not strictly increasing within the object")
    for pos, (number, start, description) in enumerate(rows):
        end = (rows[pos + 1][1] - 1) if pos + 1 < len(rows) else object_size - 1
        tokens = description.split(":")
        for field, (short_name, level) in FIELD_PATTERNS.items():
            if short_name in tokens and level in tokens:
                if field in selected:
                    raise GfsError(f"duplicate {field} in GFS index")
                selected[field] = IndexRecord(number, start, end, description)
    missing = sorted(set(FIELD_PATTERNS) - set(selected))
    if missing:
        raise GfsError(f"GFS index is missing fields: {', '.join(missing)}")
    return selected


def _candidate_inits(as_of: datetime, days: int = 10):
    first = as_of.replace(hour=(as_of.hour // 6) * 6)
    for offset in range(days * 4 + 1):
        yield first - timedelta(hours=offset * 6)


def _inspect_cycle(http: Http, init: datetime, as_of: datetime, leads: range) -> list[dict[str, Any]]:
    def inspect(lead: int) -> dict[str, Any] | None:
        url, index_url = _urls(init, lead)
        try:
            obj_headers = http.head(url)
            idx_headers = http.head(index_url)
        except GfsError:
            return None
        available = _http_time(obj_headers, url)
        index_available = _http_time(idx_headers, index_url)
        if available > as_of or index_available > as_of:
            return None
        return {
            "url": url,
            "index_url": index_url,
            "available_at": available,
            "index_available_at": index_available,
            "size": _content_length(obj_headers, url),
            "lead": lead,
        }

    with ThreadPoolExecutor(max_workers=min(8, len(leads))) as pool:
        objects = list(pool.map(inspect, leads))
    return [] if any(item is None for item in objects) else objects  # type: ignore[return-value]


def _load_object(http: Http, decoder: Decoder, item: dict[str, Any], init: datetime) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    idx_body, _ = http.get(item["index_url"])
    records = _parse_index(idx_body.decode("utf-8"), item["size"])
    decoded: dict[str, dict[str, Any]] = {tid: {} for tid in TURBINES}
    message_meta = []
    aggregate = hashlib.sha256()
    for field, record in records.items():
        message, _ = http.get(item["url"], (record.start, record.end))
        digest = hashlib.sha256(message).hexdigest()
        aggregate.update(field.encode())
        aggregate.update(message)
        message_meta.append({
            "field": field,
            "record": record.number,
            "byte_range": [record.start, record.end],
            "sha256": digest,
            "bytes": len(message),
        })
        for turbine_id, (lat, lon) in TURBINES.items():
            value = decoder.decode(message, lat, lon)
            expected_valid = init + timedelta(hours=item["lead"])
            if value["init_time"] != _iso(init) or value["valid_time"] != _iso(expected_valid):
                raise GfsError(f"GRIB time metadata does not match {item['url']}")
            raw = float(value["value"])
            units = value["units"]
            if field == "temperature_2m":
                if units in ("K", "kelvin"):
                    raw -= 273.15
                elif units not in ("C", "deg C", "celsius"):
                    raise GfsError(f"unsupported temperature units: {units}")
            elif units not in ("m s**-1", "m/s", "m s-1"):
                raise GfsError(f"unsupported wind units: {units}")
            if not math.isfinite(raw):
                raise GfsError(f"non-finite {field} from {item['url']}")
            decoded[turbine_id][field] = raw
            decoded[turbine_id].setdefault("grid", {})[field] = {
                "latitude": value["grid_latitude"],
                "longitude": value["grid_longitude"],
                "distance_km": value.get("distance", 0.0),
            }
    metadata = {
        "url": item["url"],
        "index_url": item["index_url"],
        "available_at": _iso(item["available_at"]),
        "index_available_at": _iso(item["index_available_at"]),
        "index_sha256": hashlib.sha256(idx_body).hexdigest(),
        "index_bytes": len(idx_body),
        "content_length": item["size"],
        "sha256": aggregate.hexdigest(),
        "sha256_scope": "field-name plus selected GRIB messages in field order",
        "messages": message_meta,
    }
    return metadata, decoded


def build_snapshot(
    as_of: datetime | str,
    horizon: int,
    cache_dir: Path | str,
    *,
    http_client: Http | None = None,
    decoder: Decoder | None = None,
) -> dict[str, Any]:
    """Build or load a 24–48 hour archived GFS snapshot.

    Every object and its index must have ``Last-Modified <= as_of``. The newest
    complete six-hourly cycle is chosen. Only indexed message byte ranges are
    fetched from GRIB objects.
    """
    origin = _parse_time(as_of)
    if isinstance(horizon, bool) or horizon not in (24, 48):
        raise ValueError("horizon must be 24 or 48 hours")
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    cache_path = cache / f"noaa-gfs-{origin:%Y%m%dT%H%M%SZ}-h{horizon}.json"
    checksum_path = cache_path.with_suffix(".json.sha256")
    if cache_path.exists():
        if not checksum_path.exists():
            raise GfsError(f"cached snapshot checksum is missing: {checksum_path}")
        raw = cache_path.read_bytes()
        expected = checksum_path.read_text(encoding="ascii").strip()
        actual = hashlib.sha256(raw).hexdigest()
        if not re.fullmatch(r"[0-9a-f]{64}", expected) or actual != expected:
            raise GfsError(f"cached snapshot checksum mismatch: {cache_path}")
        snapshot = json.loads(raw)
        from backend.replay import ReplayContext, validate_weather_snapshot

        return validate_weather_snapshot(snapshot, ReplayContext(origin, horizon))

    http = http_client or UrllibHttp()
    leads_by_init: tuple[datetime, range, list[dict[str, Any]]] | None = None
    for init in _candidate_inits(origin):
        first_lead = int((origin - init).total_seconds() // 3600)
        leads = range(first_lead, first_lead + horizon)
        if first_lead < 0 or leads.stop - 1 > 120:
            continue
        objects = _inspect_cycle(http, init, origin, leads)
        if objects:
            leads_by_init = init, leads, objects
            break
    if leads_by_init is None:
        raise GfsError("no complete GFS cycle was published by as_of and covers the requested horizon")
    init, _, inspected = leads_by_init
    actual_decoder = decoder or EccodesDecoder()
    with ThreadPoolExecutor(max_workers=min(6, len(inspected))) as pool:
        loaded = list(pool.map(lambda item: _load_object(http, actual_decoder, item, init), inspected))

    points = []
    object_metadata = []
    for item, (metadata, values) in zip(inspected, loaded):
        object_metadata.append(metadata)
        target_start = init + timedelta(hours=item["lead"])
        for turbine_id in TURBINES:
            fields = values[turbine_id]
            points.append({
                "turbine_id": turbine_id,
                "target_start": _iso(target_start),
                "target_end": _iso(target_start + timedelta(hours=1)),
                "wind_u_100m": fields["wind_u_100m"],
                "wind_v_100m": fields["wind_v_100m"],
                "wind_speed_100m": math.hypot(fields["wind_u_100m"], fields["wind_v_100m"]),
                "wind_u_10m": fields["wind_u_10m"],
                "wind_v_10m": fields["wind_v_10m"],
                "wind_speed_10m": math.hypot(fields["wind_u_10m"], fields["wind_v_10m"]),
                "temperature_2m": fields["temperature_2m"],
                "grid": fields["grid"],
            })
    snapshot = {
        "provider": "noaa_gfs",
        "init_time": _iso(init),
        "available_at": max(
            max(x["available_at"], x["index_available_at"])
            for x in object_metadata
        ),
        "availability_basis": "s3_last_modified",
        "weather_semantics": "instantaneous forecast at target_start; not an hourly average",
        "units": {"wind": "m/s", "temperature": "degC"},
        "points": points,
        "objects": object_metadata,
    }
    from backend.replay import ReplayContext, validate_weather_snapshot

    validate_weather_snapshot(snapshot, ReplayContext(origin, horizon))
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    raw = (payload + "\n").encode()
    cache_path.write_bytes(raw)
    checksum_path.write_text(hashlib.sha256(raw).hexdigest() + "\n", encoding="ascii")
    return snapshot

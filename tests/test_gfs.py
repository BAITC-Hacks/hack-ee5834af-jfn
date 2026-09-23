import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.weather.gfs import GfsError, MAX_MESSAGE_BYTES, UrllibHttp, build_snapshot

AS_OF = datetime(2026, 2, 6, tzinfo=timezone.utc)
HTTP_DATE = "Thu, 05 Feb 2026 23:00:00 GMT"


class FakeHttp:
    def __init__(self, *, late=False, missing=False):
        self.late, self.missing, self.ranges = late, missing, []

    def head(self, url):
        if self.missing:
            raise GfsError("missing")
        modified = "Fri, 06 Feb 2026 01:00:00 GMT" if "gfs.20260206/00" in url else HTTP_DATE
        return {"last-modified": "Fri, 06 Feb 2026 01:00:00 GMT" if self.late else modified, "content-length": "500"}

    def get(self, url, byte_range=None):
        if url.endswith(".idx"):
            body = "".join((
                "1:0:d=2026020518:UGRD:100 m above ground:6 hour fcst:\n",
                "2:100:d=2026020518:VGRD:100 m above ground:6 hour fcst:\n",
                "3:200:d=2026020518:UGRD:10 m above ground:6 hour fcst:\n",
                "4:300:d=2026020518:VGRD:10 m above ground:6 hour fcst:\n",
                "5:400:d=2026020518:TMP:2 m above ground:6 hour fcst:\n",
            )).encode()
            return body, {"last-modified": HTTP_DATE}
        if byte_range is None:
            raise AssertionError("GRIB request did not use Range")
        self.ranges.append(byte_range)
        if byte_range[1] - byte_range[0] + 1 > MAX_MESSAGE_BYTES:
            raise AssertionError("range exceeded safety limit")
        return f"{int(url.rsplit('f', 1)[1])}:{byte_range[0]}".encode(), {}


class FakeDecoder:
    def decode(self, message, latitude, longitude):
        lead, start = map(int, message.split(b":"))
        valid = datetime(2026, 2, 5, 18, tzinfo=timezone.utc) + timedelta(hours=lead)
        return {
            "value": {0: 3.0, 100: 4.0, 200: 1.0, 300: 2.0, 400: 280.0}[start],
            "units": "K" if start == 400 else "m s**-1",
            "grid_latitude": 43.75, "grid_longitude": 78.5, "distance": 12.0,
            "init_time": "2026-02-05T18:00:00Z",
            "valid_time": valid.isoformat().replace("+00:00", "Z"),
            "short_name": "x", "level": 0,
        }


class GfsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cache = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_builds_range_only_snapshot_and_caches(self):
        http = FakeHttp()
        snapshot = build_snapshot(AS_OF, 24, self.cache, http_client=http, decoder=FakeDecoder())
        self.assertEqual(snapshot["provider"], "noaa_gfs")
        self.assertEqual(snapshot["init_time"], "2026-02-05T18:00:00Z")
        self.assertEqual(len(snapshot["points"]), 48)
        self.assertEqual(snapshot["points"][0]["wind_speed_100m"], 5.0)
        self.assertAlmostEqual(snapshot["points"][0]["temperature_2m"], 6.85)
        self.assertEqual(snapshot["points"][0]["grid"]["temperature_2m"]["latitude"], 43.75)
        self.assertEqual(len(http.ranges), 120)
        self.assertTrue(all(obj["messages"] and obj["sha256"] for obj in snapshot["objects"]))
        self.assertTrue(all(obj["index_sha256"] and obj["index_bytes"] for obj in snapshot["objects"]))
        self.assertEqual(build_snapshot(AS_OF, 24, self.cache), snapshot)

    def test_rejects_naive_origin(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            build_snapshot(datetime(2026, 2, 6), 24, self.cache, http_client=FakeHttp(), decoder=FakeDecoder())

    def test_late_or_missing_objects_do_not_form_cycle(self):
        with self.assertRaisesRegex(GfsError, "no complete"):
            build_snapshot(AS_OF, 24, self.cache, http_client=FakeHttp(late=True), decoder=FakeDecoder())
        with self.assertRaisesRegex(GfsError, "no complete"):
            build_snapshot(AS_OF, 24, self.cache, http_client=FakeHttp(missing=True), decoder=FakeDecoder())

    def test_rejects_tampered_cache(self):
        build_snapshot(AS_OF, 24, self.cache, http_client=FakeHttp(), decoder=FakeDecoder())
        next(self.cache.glob("*.json")).write_text("{}\n")
        with self.assertRaisesRegex(GfsError, "checksum mismatch"):
            build_snapshot(AS_OF, 24, self.cache)

    def test_urllib_refuses_full_grib_download(self):
        with self.assertRaisesRegex(GfsError, "non-range"):
            UrllibHttp().get("https://example.test/file.grib2")


if __name__ == "__main__":
    unittest.main()

import math
import unittest
from datetime import datetime, timezone

from backend.replay import (
    ReplayContext,
    parse_timestamp,
    require_available_at,
    select_scada,
    validate_artifact_cutoffs,
    validate_weather_snapshot,
)


def weather_snapshot(context):
    points = []
    for turbine_id in ("T1", "T2"):
        for start, end in context.intervals:
            points.append(
                {
                    "turbine_id": turbine_id,
                    "target_start": start.isoformat(),
                    "target_end": end.isoformat(),
                    "wind_u_100m": 1.0,
                    "wind_v_100m": 2.0,
                    "wind_speed_100m": 3.0,
                    "wind_u_10m": 1.0,
                    "wind_v_10m": 2.0,
                    "wind_speed_10m": 3.0,
                    "temperature_2m": 270.0,
                }
            )
    return {
        "provider": "noaa_gfs",
        "init_time": "2026-01-31T18:00:00Z",
        "available_at": "2026-01-31T22:30:00Z",
        "availability_basis": "s3_last_modified",
        "weather_semantics": "instantaneous forecast at target_start; not an hourly average",
        "units": {"wind": "m/s", "temperature": "degC"},
        "points": points,
        "objects": [
            {
                "url": "https://example.test/gfs.grib2",
                "index_url": "https://example.test/gfs.grib2.idx",
                "available_at": "2026-01-31T22:30:00Z",
                "index_available_at": "2026-01-31T22:29:00Z",
                "sha256": "a" * 64,
                "index_sha256": "b" * 64,
            }
        ],
    }


class ReplayContextTests(unittest.TestCase):
    def test_offsets_identify_same_utc_origin(self):
        first = ReplayContext(parse_timestamp("2026-02-01T05:00:00+05:00"), 48)
        second = ReplayContext(parse_timestamp("2026-02-01T00:00:00Z"), 48)
        self.assertEqual(first, second)
        self.assertEqual(first.intervals[-1][1].isoformat(), "2026-02-03T00:00:00+00:00")

    def test_rejects_naive_unaligned_and_unsupported_horizon(self):
        with self.assertRaises(ValueError):
            parse_timestamp("2026-02-01T00:00:00")
        with self.assertRaises(ValueError):
            ReplayContext(parse_timestamp("2026-02-01T00:30:00Z"))
        with self.assertRaises(ValueError):
            ReplayContext(parse_timestamp("2026-02-01T00:00:00Z"), 36)


class TemporalGuardTests(unittest.TestCase):
    def setUp(self):
        self.context = ReplayContext(datetime(2026, 2, 1, tzinfo=timezone.utc), 24)

    def test_scada_requires_both_interval_and_availability_before_origin(self):
        allowed = {"id": "old", "interval_end": "2026-01-31T23:00:00Z", "available_at": "2026-02-01T00:00:00Z"}
        late_interval = {"id": "future", "interval_end": "2026-02-01T01:00:00Z", "available_at": "2026-01-31T23:00:00Z"}
        late_release = {"id": "late", "interval_end": "2026-01-31T23:00:00Z", "available_at": "2026-02-01T00:00:01Z"}
        self.assertEqual(select_scada([allowed, late_interval, late_release], self.context), (allowed,))

    def test_future_scada_does_not_change_selected_inputs(self):
        history = [{"id": 1, "interval_end": "2026-01-31T23:00:00Z", "available_at": "2026-01-31T23:05:00Z"}]
        baseline = select_scada(history, self.context)
        history.append({"id": 2, "interval_end": "2026-02-01T01:00:00Z", "available_at": "2026-02-01T01:05:00Z"})
        self.assertEqual(select_scada(history, self.context), baseline)

    def test_unknown_and_future_availability_never_pass(self):
        for value in (None, "bad", "2026-02-01T00:00:01Z"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                require_available_at(value, self.context)

    def test_all_artifact_cutoffs_are_bounded(self):
        valid = {"model": {"training_cutoff": "2026-01-30T00:00:00Z", "data_cutoff": "2026-01-31T00:00:00Z"}}
        self.assertIs(validate_artifact_cutoffs(valid, self.context), valid)
        for key in ("training_cutoff", "preprocessing_cutoff", "calibration_cutoff", "data_cutoff"):
            artifact = {"training_cutoff": "2026-01-30T00:00:00Z", key: "2026-02-02T00:00:00Z"}
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_artifact_cutoffs(artifact, self.context)

    def test_weather_validates_exact_coverage_and_availability(self):
        snapshot = weather_snapshot(self.context)
        self.assertIs(validate_weather_snapshot(snapshot, self.context), snapshot)
        snapshot["objects"][0]["index_available_at"] = "2026-02-01T00:00:01Z"
        with self.assertRaises(ValueError):
            validate_weather_snapshot(snapshot, self.context)

    def test_snapshot_availability_includes_later_index(self):
        snapshot = weather_snapshot(self.context)
        snapshot["objects"][0]["index_available_at"] = "2026-01-31T23:00:00Z"
        snapshot["available_at"] = "2026-01-31T23:00:00Z"
        self.assertIs(validate_weather_snapshot(snapshot, self.context), snapshot)

    def test_init_time_alone_is_not_proof_of_availability(self):
        snapshot = weather_snapshot(self.context)
        snapshot["available_at"] = None
        with self.assertRaises(ValueError):
            validate_weather_snapshot(snapshot, self.context)

    def test_rejects_unknown_units_semantics_or_source_hashes(self):
        mutations = (
            ("units", None),
            ("weather_semantics", None),
            ("object:index_url", None),
            ("object:sha256", "abc123"),
            ("object:index_sha256", None),
        )
        for key, value in mutations:
            snapshot = weather_snapshot(self.context)
            if key.startswith("object:"):
                snapshot["objects"][0][key.split(":", 1)[1]] = value
            else:
                snapshot[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_weather_snapshot(snapshot, self.context)

    def test_rejects_missing_duplicate_extra_and_nonfinite_points(self):
        for mutation in ("missing", "duplicate", "extra", "nan", "negative_speed"):
            snapshot = weather_snapshot(self.context)
            if mutation == "missing":
                snapshot["points"].pop()
            elif mutation == "duplicate":
                snapshot["points"].append(dict(snapshot["points"][0]))
            elif mutation == "extra":
                point = dict(snapshot["points"][0])
                point["turbine_id"] = "T3"
                snapshot["points"].append(point)
            elif mutation == "nan":
                snapshot["points"][0]["temperature_2m"] = math.nan
            else:
                snapshot["points"][0]["wind_speed_100m"] = -1
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_weather_snapshot(snapshot, self.context)


if __name__ == "__main__":
    unittest.main()

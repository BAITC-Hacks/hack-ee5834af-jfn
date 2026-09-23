import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from backend.forecasting.gfs_features import FEATURE_ORDER, make_gfs_features
from backend.replay import ReplayContext, validate_weather_snapshot
from ml.build_gfs_training_dataset import build, hourly_labels, rows_from_snapshot
from ml.train_gfs import split_rows

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "data/fixtures/noaa-gfs-20251110T060000Z-h48.json"
ORIGIN = datetime(2025, 11, 10, 6, tzinfo=timezone.utc)


class GfsTrainingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_shared_features_distinguish_two_leads_and_exclude_observed_weather(self):
        point = dict(self.snapshot["points"][0])
        point["wind_speed"] = 9999  # observed target-time SCADA must be ignored
        point["temperature"] = -9999
        values = make_gfs_features(point, ORIGIN, self.snapshot["init_time"])
        self.assertEqual(tuple(values), FEATURE_ORDER)
        self.assertNotIn("wind_speed", values)
        self.assertNotIn("temperature", values)
        self.assertEqual(values["origin_lead_hours"], 0)
        self.assertEqual(values["gfs_lead_hours"], 6)
        self.assertEqual(values["gfs_init_hour"], 0)

    def test_future_object_and_incomplete_horizon_are_rejected(self):
        snapshot = json.loads(json.dumps(self.snapshot))
        snapshot["objects"][0]["available_at"] = "2025-11-10T07:00:00Z"
        with self.assertRaisesRegex(ValueError, "not available"):
            validate_weather_snapshot(snapshot, ReplayContext(ORIGIN, 48))
        snapshot = json.loads(json.dumps(self.snapshot))
        snapshot["points"].pop()
        with self.assertRaisesRegex(ValueError, "coverage mismatch"):
            validate_weather_snapshot(snapshot, ReplayContext(ORIGIN, 48))

    def test_purge_overlapping_targets_at_split_boundary(self):
        rows = [
            {"origin_time": "2025-12-31T06:00:00Z", "target_time": "2026-01-01T05:00:00Z",
             "label_available_at": "2026-01-01T06:00:00Z", "turbine_id": 1.0},
            {"origin_time": "2025-12-25T06:00:00Z", "target_time": "2025-12-25T08:00:00Z",
             "label_available_at": "2025-12-25T09:00:00Z", "turbine_id": 1.0},
            {"origin_time": "2025-12-25T06:00:00Z", "target_time": "2025-12-25T08:00:00Z",
             "label_available_at": "2025-12-25T09:00:00Z", "turbine_id": 2.0},
            {"origin_time": "2026-01-05T06:00:00Z", "target_time": "2026-01-05T08:00:00Z",
             "label_available_at": "2026-01-05T09:00:00Z", "turbine_id": 1.0},
            {"origin_time": "2026-01-05T06:00:00Z", "target_time": "2026-01-05T08:00:00Z",
             "label_available_at": "2026-01-05T09:00:00Z", "turbine_id": 2.0},
        ]
        train, valid, final, meta = split_rows(pd.DataFrame(rows),
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 30, 18, tzinfo=timezone.utc))
        self.assertEqual(len(train), 2)
        self.assertEqual(len(valid), 2)
        self.assertEqual(meta["purged_train_rows"], 1)
        self.assertTrue(set(train.origin_time).isdisjoint(set(valid.origin_time)))

    def test_committed_real_fixture_builds_reproducible_labeled_dataset(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            cache = temp / "cache"
            cache.mkdir()
            shutil.copyfile(FIXTURE, cache / FIXTURE.name)
            shutil.copyfile(FIXTURE.with_suffix(".json.sha256"), cache / f"{FIXTURE.name}.sha256")
            manifest = build(ORIGIN, datetime(2025, 11, 11, 6, tzinfo=timezone.utc), 1,
                ROOT / "data/training/turbine_1.csv", ROOT / "data/training/turbine_2.csv",
                ROOT / "ml/config.brev-assumptions.json", cache, temp / "out", workers=1)
            self.assertEqual(manifest["origins_complete"], 1)
            self.assertEqual(manifest["rows"], 96)
            self.assertEqual(manifest["missing_power_labels"], 0)
            frame = pd.read_csv(temp / "out/training_rows.csv")
            self.assertEqual(tuple(frame.loc[:, list(FEATURE_ORDER)].columns), FEATURE_ORDER)
            self.assertNotIn("wind_speed", frame.columns)
            self.assertNotIn("temperature", frame.columns)
            self.assertEqual(manifest["dataset_sha256"],
                             json.loads((temp / "out/manifest.json").read_text())["dataset_sha256"])


if __name__ == "__main__":
    unittest.main()

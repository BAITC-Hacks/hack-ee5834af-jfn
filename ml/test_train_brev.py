"""Focused checks for the hourly SCADA training contract.

Run: python -m unittest -v test_train_brev.py
"""
import json
import tempfile
import unittest
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd

import train_brev as tb


CONFIG = {"timestamp_format": "%Y-%m-%d %H:%M:%S", "source_timezone": "UTC",
          "first_forecast_origin": "2026-01-31 00:00:00", "scada_delay_hours": 0,
          "wind_speed_max_mps": 30}


def hourly_rows(hour: str, wind: float = 6.0) -> pd.DataFrame:
    times = pd.date_range(hour, periods=6, freq="10min")
    return pd.DataFrame({"ID": range(1, 7), "Статистическое время": times.strftime("%Y-%m-%d %H:%M:%S"),
                         "Средняя скорость ветра(m/s)": wind,
                         "Нормализованная активная мощность": 0.4,
                         "Средняя температура окружающей среды(°C)": 15.0})


class TrainingContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "turbine.csv"

    def test_headers_and_strict_timestamp(self):
        rows = hourly_rows("2025-10-31 22:00:00")
        rows.to_csv(self.path, index=False)
        hourly, report = tb.prepare(self.path, 1, CONFIG)
        self.assertEqual(len(hourly), 1)
        self.assertEqual(report["retained_hours"], 1)
        rows.iloc[0, 1] = "31/10/2025 22:00:00"
        rows.to_csv(self.path, index=False)
        with self.assertRaisesRegex(ValueError, "Invalid/missing timestamp"):
            tb.prepare(self.path, 1, CONFIG)
        rows = hourly_rows("2025-10-31 22:00:00").rename(columns={"Статистическое время": "wrong"})
        rows.to_csv(self.path, index=False)
        with self.assertRaisesRegex(ValueError, "Unexpected CSV columns"):
            tb.prepare(self.path, 1, CONFIG)

    def test_incomplete_and_off_grid_hours(self):
        rows = hourly_rows("2025-10-31 22:00:00")
        rows.loc[1, "Статистическое время"] = "2025-10-31 22:01:00"
        rows.to_csv(self.path, index=False)
        hourly, report = tb.prepare(self.path, 1, CONFIG)
        self.assertTrue(hourly.empty)
        self.assertEqual(report["off_grid_hours"], 1)
        self.assertEqual(report["retained_hours"], 0)

    def test_duplicate_resolution_and_outlier(self):
        rows = hourly_rows("2025-10-31 22:00:00")
        pd.concat([rows, rows.iloc[[0]]], ignore_index=True).to_csv(self.path, index=False)
        hourly, report = tb.prepare(self.path, 1, CONFIG)
        self.assertEqual(len(hourly), 1)
        self.assertEqual(report["identical_duplicate_extra_rows_removed"], 1)
        conflicting = rows.iloc[[0]].copy()
        conflicting.iloc[0, 2] = 9.0
        pd.concat([rows, conflicting], ignore_index=True).to_csv(self.path, index=False)
        hourly, report = tb.prepare(self.path, 1, CONFIG)
        self.assertTrue(hourly.empty)
        self.assertEqual(report["conflicting_duplicate_groups"], 1)
        rows.iloc[0, 2] = 999.0
        rows.to_csv(self.path, index=False)
        hourly, report = tb.prepare(self.path, 1, CONFIG)
        self.assertTrue(hourly.empty)
        self.assertEqual(report["excluded_row_reasons"]["wind_out_of_range"], 1)

    def test_full_validation_and_selection_cutoff(self):
        hours = ["2025-10-31 21:00:00", "2025-10-31 22:00:00", "2025-11-01 00:00:00",
                 "2026-01-30 23:00:00", "2026-01-31 00:00:00", "2026-01-31 23:00:00",
                 "2026-02-01 00:00:00"]
        data = pd.DataFrame([{"timestamp": pd.Timestamp(hour), "wind_speed": 6.0,
                              "temperature": 15.0, "power": 0.4, "turbine_id": turbine}
                             for turbine in (1, 2) for hour in hours])
        train, valid, selection, final, meta = tb.split_data(data, CONFIG)
        self.assertEqual(len(train), 4)
        self.assertEqual(len(valid), 8)
        self.assertEqual(len(selection), 4)
        self.assertEqual(valid.timestamp.max(), pd.Timestamp("2026-01-31 23:00:00"))
        self.assertEqual(selection.timestamp.max(), pd.Timestamp("2026-01-30 23:00:00"))
        self.assertEqual(final.timestamp.max(), pd.Timestamp("2026-01-30 23:00:00"))
        self.assertEqual(meta["validation_rows"], {"turbine_1": 4, "turbine_2": 4})
        without_train = data.loc[~((data.turbine_id == 1) & (data.timestamp < tb.TRAIN_END))]
        with self.assertRaisesRegex(ValueError, "train needs at least 2 rows"):
            tb.split_data(without_train, CONFIG)

    def test_strict_json_and_undefined_r2(self):
        result = tb.score(np.array([0.5]), np.array([-0.2]))
        self.assertEqual(result["r2"], None)
        self.assertEqual(result["mae"], 0.5)
        self.assertIn('"r2": null', tb.strict_json(result))
        with self.assertRaises(ValueError):
            tb.strict_json({"bad": float("nan")})

    def test_same_rows_clip_and_cpu_round_trip(self):
        frame = pd.DataFrame({"timestamp": pd.to_datetime(["2025-11-01 00:00:00", "2025-11-01 01:00:00"]),
                              "wind_speed": [5.0, 7.0], "temperature": [10.0, 11.0],
                              "power": [0.2, 0.8], "turbine_id": [1, 2]})
        curves = {str(i): {"global_mean": -0.2, "bins": {}} for i in (1, 2)}
        model = lgb.LGBMRegressor(n_estimators=3, min_child_samples=1, verbosity=-1, n_jobs=1)
        model.fit(tb.make_features(frame), frame.power)
        candidates = {"power_curve": {"curves": curves},
                      "pooled_lgbm": {"models": {"pooled": model}},
                      "separate_lgbm": {"models": {"1": model, "2": model}}}
        metrics, winner = tb.compare_candidates(candidates, frame)
        hashes = {metrics[k]["validation_key_sha256"] for k in candidates}
        self.assertEqual(len(hashes), 1)
        self.assertIn(winner, candidates)
        curve_bundle = {"kind": "power_curve", "features": tb.FEATURES, "curves": curves}
        self.assertTrue(np.array_equal(tb.predict_bundle(curve_bundle, frame), [0, 0]))
        model_bundle = {"kind": "pooled_lgbm", "features": tb.FEATURES, "models": {"pooled": model}}
        before = tb.predict_bundle(model_bundle, frame)
        tb.save_and_check(model_bundle, frame, Path(self.temp.name))
        path = Path(self.temp.name) / "best_model.joblib"
        after = tb.predict_bundle(joblib.load(path), frame)
        np.testing.assert_allclose(before, after, atol=0, rtol=0)
        self.assertTrue(np.all((after >= 0) & (after <= 1)))
        with self.assertRaisesRegex(ValueError, "both turbines"):
            tb.save_and_check(model_bundle, frame.iloc[[0]], Path(self.temp.name))


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import replay
from tests.test_temporal import weather_snapshot
from backend.replay import ReplayContext, parse_timestamp


class ReplayCliTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        context = ReplayContext(parse_timestamp("2026-02-01T00:00:00Z"), 24)
        self.snapshot = self.root / "snapshot.json"
        self.snapshot.write_text(json.dumps(weather_snapshot(context)), encoding="utf-8")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_offline_cli_writes_honest_weather_readiness_audit(self):
        output = self.root / "out"
        status = replay.main(
            [
                "--as-of", "2026-02-01T05:00:00+05:00",
                "--horizon", "24",
                "--snapshot", str(self.snapshot),
                "--output-dir", str(output),
                "--executed-at", "2026-09-23T00:00:00Z",
            ]
        )
        self.assertEqual(status, 0)
        audit = json.loads((output / "weather-inputs-audit.json").read_text())
        self.assertEqual(audit["stage"], "weather_inputs_ready")
        self.assertIsNone(audit["forecast_power"])
        self.assertEqual(audit["weather"]["point_count"], 48)
        self.assertEqual(audit["scada"]["status"], "NOT_VERIFIED")
        self.assertEqual(audit["model"]["status"], "NOT_VERIFIED")

    def test_scada_uses_explicit_latency_and_excludes_future_rows(self):
        scada = self.root / "scada.csv"
        scada.write_text(
            "interval_end,power\n"
            "2026-01-31T23:00:00Z,1\n"
            "2026-02-01T00:00:00Z,2\n",
            encoding="utf-8",
        )
        output = self.root / "out"
        replay.main(
            [
                "--as-of", "2026-02-01T00:00:00Z",
                "--horizon", "24",
                "--snapshot", str(self.snapshot),
                "--output-dir", str(output),
                "--scada-csv", str(scada),
                "--scada-latency-minutes", "10",
                "--executed-at", "2026-09-23T00:00:00Z",
            ]
        )
        audit = json.loads((output / "weather-inputs-audit.json").read_text())
        self.assertEqual(audit["scada"]["selected_rows"], 1)
        self.assertEqual(
            audit["scada"]["latest_interval_end"], "2026-01-31T23:00:00+00:00"
        )

    def test_model_with_future_cutoff_fails_closed(self):
        metadata = self.root / "model.json"
        metadata.write_text(
            json.dumps({"model_id": "bad", "training_cutoff": "2026-02-02T00:00:00Z"}),
            encoding="utf-8",
        )
        with self.assertRaises(SystemExit) as caught:
            replay.main(
                [
                    "--as-of", "2026-02-01T00:00:00Z",
                    "--horizon", "24",
                    "--snapshot", str(self.snapshot),
                    "--output-dir", str(self.root / "out"),
                    "--model-metadata", str(metadata),
                    "--executed-at", "2026-09-23T00:00:00Z",
                ]
            )
        self.assertEqual(caught.exception.code, 2)

    def test_live_archive_failure_is_reported_without_traceback(self):
        output = self.root / "out"
        with patch(
            "backend.weather.gfs.build_snapshot",
            side_effect=RuntimeError("archive unavailable"),
        ), self.assertRaises(SystemExit) as caught:
            replay.main(
                [
                    "--as-of", "2026-02-01T00:00:00Z",
                    "--horizon", "24",
                    "--output-dir", str(output),
                    "--executed-at", "2026-09-23T00:00:00Z",
                ]
            )
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()

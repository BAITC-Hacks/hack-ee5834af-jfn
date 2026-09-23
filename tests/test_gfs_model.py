import json
import math
import shutil
import tempfile
import unittest
from pathlib import Path

from backend.forecasting import ModelBlocked, load_bundle, predict
from backend.replay import ReplayContext, parse_timestamp
from backend.storage import Store
from backend.workflow import ForecastService
from backend.agent.operator import ForecastOperator

ROOT = Path(__file__).parents[1]
MODEL = ROOT / "artifacts/models/gfs-pooled-lgbm-mvp-20260131"
CURVE = ROOT / "artifacts/models/gfs-power-curve-mvp-20260131"
SNAPSHOT = ROOT / "data/fixtures/noaa-gfs-20260206T060000Z-h48.json"
ORIGIN = "2026-02-06T06:00:00Z"


class RecordedResponses:
    def __init__(self, context):
        self.calls = iter([("get_weather_forecast", {"snapshot_id": context.candidates[0]}),
                           ("validate_inputs", {}), ("run_forecast", {"model_id": context.model_id}),
                           ("validate_forecast", {}), ("publish_forecast", {})])
    def respond(self, *_args):
        try: name, args = next(self.calls)
        except StopIteration: return {"output": []}
        return {"id":"recorded", "output":[{"type":"function_call", "name":name,
                "arguments":json.dumps(args), "call_id":name}]}


def governed(service):
    return lambda svc, context, run_id, attempt: ForecastOperator(svc, context, RecordedResponses(context), run_id=run_id, worker_attempt=attempt)


class GfsModelTests(unittest.TestCase):
    def test_saved_cpu_bundle_predicts_complete_real_48h_snapshot(self):
        snapshot = json.loads(SNAPSHOT.read_text())
        bundle = load_bundle(MODEL, MODEL.name, ReplayContext(parse_timestamp(ORIGIN), 48))
        points = predict(bundle, snapshot["points"], snapshot["init_time"])
        self.assertEqual(len(points), 96)
        self.assertTrue(all(math.isfinite(p["normalized_power"]) and
                            0 <= p["normalized_power"] <= 1 for p in points))
        self.assertIn("did not beat", bundle["warning"])

    def test_selected_power_curve_bundle_predicts_96_points(self):
        snapshot = json.loads(SNAPSHOT.read_text())
        bundle = load_bundle(CURVE, CURVE.name, ReplayContext(parse_timestamp(ORIGIN), 48))
        points = predict(bundle, snapshot["points"], snapshot["init_time"])
        self.assertEqual(len(points), 96)
        self.assertTrue(all(0 <= p["normalized_power"] <= 1 for p in points))
        with self.assertRaisesRegex(ModelBlocked, "outside the validated GFS schedule"):
            load_bundle(CURVE, CURVE.name, ReplayContext(parse_timestamp("2026-02-06T00:00:00Z"), 48))

    def test_future_cutoff_and_tampered_schema_or_model_block(self):
        with self.assertRaisesRegex(ModelBlocked, "future training_cutoff"):
            load_bundle(MODEL, MODEL.name,
                ReplayContext(parse_timestamp("2026-01-29T06:00:00Z"), 48))
        with tempfile.TemporaryDirectory() as temp:
            copy = Path(temp) / MODEL.name
            shutil.copytree(MODEL, copy)
            (copy / "model.txt").write_text("corrupt")
            with self.assertRaisesRegex(ModelBlocked, "checksum mismatch"):
                load_bundle(copy, MODEL.name, ReplayContext(parse_timestamp(ORIGIN), 48))
        with tempfile.TemporaryDirectory() as temp:
            copy = Path(temp) / MODEL.name
            shutil.copytree(MODEL, copy)
            schema = json.loads((copy / "feature_schema.json").read_text())
            schema["feature_order"].reverse()
            (copy / "feature_schema.json").write_text(json.dumps(schema))
            with self.assertRaisesRegex(ModelBlocked, "feature schema"):
                load_bundle(copy, MODEL.name, ReplayContext(parse_timestamp(ORIGIN), 48))

    def test_real_snapshot_flows_through_persisted_backend_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshots, models = root / "snapshots", root / "models"
            snapshots.mkdir(); models.mkdir()
            shutil.copyfile(SNAPSHOT, snapshots / "gfs-feb6.json")
            shutil.copytree(MODEL, models / MODEL.name)
            service = ForecastService(Store(root / "runs.sqlite3"), snapshots, models)
            service.operator_factory = governed(service)
            run, _ = service.create_run(as_of=ORIGIN, horizon=48,
                                        model_id=MODEL.name, weather_snapshot_id="gfs-feb6")
            service.process_one()
            forecast = service.get_forecast(run["id"])
            self.assertEqual(forecast["status"], "SUCCEEDED", forecast["error"])
            self.assertEqual(len(forecast["points"]), 96)
            self.assertEqual(service.get_audit(run["id"])["details"]["model_kind"],
                             "gfs_lightgbm_v1")

    def test_selected_power_curve_flows_through_backend(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshots, models = root / "snapshots", root / "models"
            snapshots.mkdir(); models.mkdir()
            shutil.copyfile(SNAPSHOT, snapshots / "gfs-feb6-06.json")
            shutil.copytree(CURVE, models / CURVE.name)
            service = ForecastService(Store(root / "runs.sqlite3"), snapshots, models)
            service.operator_factory = governed(service)
            run, _ = service.create_run(as_of=ORIGIN, horizon=48,
                                        model_id=CURVE.name, weather_snapshot_id="gfs-feb6-06")
            service.process_one()
            forecast = service.get_forecast(run["id"])
            self.assertEqual(forecast["status"], "SUCCEEDED", forecast["error"])
            self.assertEqual(len(forecast["points"]), 96)
            self.assertEqual(service.get_audit(run["id"])["details"]["model_kind"],
                             "gfs_power_curve_v1")


if __name__ == "__main__":
    unittest.main()

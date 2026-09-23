import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import timedelta
from pathlib import Path

from backend.forecasting.adapter import FEATURES
from backend.replay import parse_timestamp
from backend.storage import Store
from backend.workflow import ForecastService, RegistryError
from backend.agent.operator import ForecastOperator
from backend.agent.tools import RunContext


FIXTURE = Path(__file__).parents[1] / "data/fixtures/noaa-gfs-20260206T000000Z-h48.json"


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.snapshots, self.models = root / "snapshots", root / "models"
        self.snapshots.mkdir(); self.models.mkdir()
        shutil.copyfile(FIXTURE,self.snapshots / "gfs-feb6.json")
        bundle = {
            "model_id":"linear-v1","training_cutoff":"2026-01-31T00:00:00Z",
            "feature_order":list(FEATURES),"intercept":0.1,
            "coefficients":{name:(0.01 if name=="wind_speed_100m" else 0.0) for name in FEATURES},
        }
        (self.models / "linear-v1.json").write_text(json.dumps(bundle))
        self.store = Store(root / "runs.sqlite3")
        self.service = ForecastService(self.store,self.snapshots,self.models)
        class Recorded:
            def __init__(self): self.calls = iter([("get_weather_forecast", {"snapshot_id":"gfs-feb6"}), ("validate_inputs", {}), ("run_forecast", {"model_id":"linear-v1"}), ("validate_forecast", {}), ("publish_forecast", {})])
            def respond(self, *_):
                try: name,args=next(self.calls)
                except StopIteration: return {"output":[]}
                return {"id":"test","output":[{"type":"function_call","name":name,"arguments":json.dumps(args),"call_id":name}]}
        self.service.operator_factory = lambda service, context, run_id, attempt: ForecastOperator(service, context, Recorded(), run_id=run_id, worker_attempt=attempt)

    def tearDown(self):
        self.temp.cleanup()

    def create(self, model="linear-v1", snapshot="gfs-feb6", as_of="2026-02-06T00:00:00Z"):
        return self.service.create_run(as_of=as_of,horizon=48,model_id=model,weather_snapshot_id=snapshot)

    def test_end_to_end_persists_exact_forecast_and_audit(self):
        run, created = self.create()
        self.assertTrue(created)
        self.assertEqual(self.service.process_one(),run["id"])
        result = self.service.get_forecast(run["id"])
        self.assertEqual(result["status"],"SUCCEEDED")
        self.assertEqual(len(result["points"]),96)
        self.assertTrue(all(0 <= p["normalized_power"] <= 1 for p in result["points"]))
        self.assertEqual(self.service.get_audit(run["id"])["details"]["point_count"],96)

    def test_missing_model_blocks_without_fabricated_points(self):
        run, _ = self.create(model="missing")
        self.service.process_one()
        result = self.service.get_forecast(run["id"])
        self.assertEqual(result["status"],"BLOCKED")
        self.assertEqual(result["points"],[])
        self.assertEqual(result["points"],[])

    def test_root_and_revision_requests_are_idempotent(self):
        first, created = self.create()
        second, repeated = self.create()
        self.assertTrue(created); self.assertFalse(repeated)
        self.assertEqual(first["id"],second["id"])
        unchanged, created1 = self.service.recalculate(first["id"],"gfs-feb6")
        self.assertFalse(created1)
        self.assertEqual(unchanged["id"],first["id"])

    def test_scheduler_creates_one_new_origin_revision_and_preserves_parent(self):
        parent, _ = self.create()
        self.service.process_one()
        before = json.dumps(self.service.get_forecast(parent["id"]),sort_keys=True)
        snapshot = json.loads(FIXTURE.read_text())
        shift = timedelta(days=1)
        for point in snapshot["points"]:
            for key in ("target_start","target_end"):
                point[key] = (parse_timestamp(point[key])+shift).isoformat()
        (self.snapshots / "gfs-feb7.json").write_text(json.dumps(snapshot))
        self.assertEqual(self.service.tick_scheduler(),1)
        self.assertEqual(self.service.tick_scheduler(),0)
        children = [r for r in self.store.list_runs() if r["parent_run_id"]==parent["id"]]
        self.assertEqual(len(children),1)
        self.assertEqual(children[0]["as_of"],"2026-02-07T00:00:00+00:00")
        self.service.process_one()
        self.assertEqual(json.dumps(self.service.get_forecast(parent["id"]),sort_keys=True),before)

    def test_expired_worker_lease_is_recovered(self):
        run, _ = self.create()
        first = self.store.claim_job(lease_seconds=1)
        with closing(self.store.connect()) as db:
            db.execute("UPDATE jobs SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?",(first["id"],))
        recovered = self.store.claim_job()
        self.assertEqual(recovered["run_id"],run["id"])
        self.assertEqual(recovered["attempts"],2)

    def test_reclaimed_computed_run_reuses_private_points(self):
        run, _ = self.create()
        first = self.store.claim_job(lease_seconds=1)
        self.service.compute_run(run["id"], first["attempts"])
        self.assertEqual(self.service.get_run(run["id"])["status"], "COMPUTED")
        self.assertEqual(len(self.store.points(run["id"])), 96)
        self.assertEqual(self.service.get_forecast(run["id"])["points"], [])
        with closing(self.store.connect()) as db:
            db.execute("UPDATE jobs SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?", (first["id"],))
        self.service.process_one()
        self.assertEqual(self.service.get_run(run["id"])["status"], "SUCCEEDED")
        self.assertEqual(len(self.service.get_forecast(run["id"])["points"]), 96)
        events = self.service.get_events(run["id"])
        self.assertEqual(len([event for event in events if event["type"] == "FORECAST_COMPUTED"]), 1)
        self.assertEqual(len([event for event in events if event["type"] == "FORECAST_READY"]), 1)

    def test_rejects_unregistered_and_path_ids(self):
        with self.assertRaises(RegistryError): self.create(snapshot="../secret")
        with self.assertRaises(RegistryError): self.create(snapshot="unknown")

    def test_live_rejects_noncurrent_origin(self):
        with self.assertRaisesRegex(ValueError,"current UTC hour"):
            self.service.create_run(as_of="2026-02-06T00:00:00Z",horizon=48,model_id="linear-v1",weather_snapshot_id="gfs-feb6",mode="live")


if __name__ == "__main__": unittest.main()

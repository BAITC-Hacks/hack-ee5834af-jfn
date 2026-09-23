import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import timedelta
from pathlib import Path

from backend.forecasting.adapter import FEATURES, ModelBlocked, load_bundle, predict
from backend.replay import ReplayContext, parse_timestamp
from backend.storage import Store
from backend.workflow import ForecastService, RegistryError


FIXTURE = Path(__file__).parents[1] / "data/fixtures/noaa-gfs-20260206T000000Z-h48.json"
SCADA = Path(__file__).parents[1] / "artifacts/models/brev-scada-pooled-lgbm-20260201"


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
        self.assertIn("missing",result["error"])

    def test_committed_scada_bundle_cpu_fixture_and_cutoff(self):
        fixture = json.loads((SCADA / "cpu_fixture.json").read_text())
        bundle = load_bundle(SCADA, "brev-scada-pooled-lgbm-20260201", ReplayContext(parse_timestamp("2026-02-06T00:00:00Z"), 48))
        actual = [item["normalized_power"] for item in predict(bundle, fixture["input"])]
        for value, expected in zip(actual, fixture["expected_normalized_power"]):
            self.assertAlmostEqual(value, expected, delta=fixture["absolute_tolerance"])
        with self.assertRaisesRegex(ModelBlocked, "future training_cutoff"):
            load_bundle(SCADA, "brev-scada-pooled-lgbm-20260201", ReplayContext(parse_timestamp("2026-01-31T18:00:00Z"), 24))

    def test_committed_scada_bundle_service_run_records_warning_and_provenance(self):
        shutil.copytree(SCADA, self.models / "brev-scada-pooled-lgbm-20260201")
        run, _ = self.create(model="brev-scada-pooled-lgbm-20260201")
        self.service.process_one()
        result = self.service.get_forecast(run["id"])
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(len(result["points"]), 96)
        self.assertTrue(all(0 <= point["normalized_power"] <= 1 for point in result["points"]))
        self.assertIn("operational forecast accuracy is unmeasured", result["warnings"][0])
        audit = self.service.get_audit(run["id"])["details"]
        self.assertEqual(audit["model_kind"], "scada_lightgbm_v1")
        self.assertEqual(audit["model_training_cutoff"], "2026-01-31T19:00:00Z")
        self.assertEqual(audit["model_sha256"], json.loads((SCADA / "metadata.json").read_text())["model_sha256"])

    def test_tampered_scada_model_blocks(self):
        bundle_dir = self.models / "brev-scada-pooled-lgbm-20260201"
        shutil.copytree(SCADA, bundle_dir)
        (bundle_dir / "model.txt").write_text("tampered")
        run, _ = self.create(model="brev-scada-pooled-lgbm-20260201")
        self.service.process_one()
        self.assertIn("SHA-256", self.service.get_forecast(run["id"])["error"])

    def test_tampered_scada_schema_blocks(self):
        bundle_dir = self.models / "brev-scada-pooled-lgbm-20260201"
        shutil.copytree(SCADA, bundle_dir)
        metadata = json.loads((bundle_dir / "feature_schema.json").read_text())
        metadata["feature_order"] = list(reversed(metadata["feature_order"]))
        (bundle_dir / "feature_schema.json").write_text(json.dumps(metadata))
        run, _ = self.create(model="brev-scada-pooled-lgbm-20260201")
        self.service.process_one()
        self.assertIn("feature schema", self.service.get_forecast(run["id"])["error"])

    def test_symlinked_scada_member_blocks(self):
        bundle_dir = self.models / "brev-scada-pooled-lgbm-20260201"
        shutil.copytree(SCADA, bundle_dir)
        (bundle_dir / "model.txt").unlink()
        try:
            (bundle_dir / "model.txt").symlink_to(Path("/etc/hosts"))
        except OSError as exc:
            if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                self.skipTest("Windows symlink privilege is unavailable")
            raise
        run, _ = self.create(model="brev-scada-pooled-lgbm-20260201")
        self.service.process_one()
        self.assertEqual(self.service.get_forecast(run["id"])["status"], "BLOCKED")
        self.assertIn("escapes registry", self.service.get_forecast(run["id"])["error"])

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

    def test_rejects_unregistered_and_path_ids(self):
        with self.assertRaises(RegistryError): self.create(snapshot="../secret")
        with self.assertRaises(RegistryError): self.create(snapshot="unknown")

    def test_live_rejects_noncurrent_origin(self):
        with self.assertRaisesRegex(ValueError,"current UTC hour"):
            self.service.create_run(as_of="2026-02-06T00:00:00Z",horizon=48,model_id="linear-v1",weather_snapshot_id="gfs-feb6",mode="live")


if __name__ == "__main__": unittest.main()

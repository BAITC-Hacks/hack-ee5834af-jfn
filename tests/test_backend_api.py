import json
import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.forecasting.adapter import FEATURES
from backend.agent.operator import ForecastOperator


FIXTURE = Path(__file__).parents[1] / "data/fixtures/noaa-gfs-20260206T000000Z-h48.json"
SCADA = Path(__file__).parents[1] / "artifacts/models/brev-scada-pooled-lgbm-20260201"


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        snapshots, models = root / "snapshots", root / "models"
        snapshots.mkdir(); models.mkdir()
        shutil.copyfile(FIXTURE,snapshots / "gfs-feb6.json")
        (models / "linear-v1.json").write_text(json.dumps({
            "model_id":"linear-v1","training_cutoff":"2026-01-31T00:00:00Z",
            "feature_order":list(FEATURES),"intercept":0.2,
            "coefficients":{name:0.0 for name in FEATURES},
        }))
        self.app = create_app(database=root / "api.sqlite3",snapshot_dir=snapshots,model_dir=models,background=False)
        class Recorded:
            def __init__(self): self.calls=iter([("get_weather_forecast",{"snapshot_id":"gfs-feb6"}),("validate_inputs",{}),("run_forecast",{"model_id":"linear-v1"}),("validate_forecast",{}),("publish_forecast",{})])
            def respond(self,*_):
                try: name,args=next(self.calls)
                except StopIteration: return {"output":[]}
                return {"id":"test","output":[{"type":"function_call","name":name,"arguments":json.dumps(args),"call_id":name}]}
        self.app.state.service.operator_factory=lambda service,context,run_id,attempt: ForecastOperator(service,context,Recorded(),run_id=run_id,worker_attempt=attempt)
        self.client = TestClient(self.app)

    def tearDown(self): self.temp.cleanup()

    def test_http_contract_and_manual_worker(self):
        body = {"as_of":"2026-02-06T00:00:00Z","horizon":48,"model_id":"linear-v1",
                "weather_snapshot_id":"gfs-feb6","mode":"replay"}
        response = self.client.post("/forecast-runs",json=body)
        self.assertEqual(response.status_code,202)
        run_id = response.json()["id"]
        repeated = self.client.post("/forecast-runs",json=body)
        self.assertFalse(repeated.json()["created"])
        self.assertEqual(repeated.json()["id"],run_id)
        self.assertEqual(self.client.get(f"/forecast-runs/{run_id}/forecast").json()["points"],[])
        self.app.state.service.process_one()
        forecast = self.client.get(f"/forecast-runs/{run_id}/forecast")
        self.assertEqual(forecast.status_code,200)
        self.assertEqual(forecast.json()["status"],"SUCCEEDED")
        self.assertEqual(len(forecast.json()["points"]),96)
        self.assertEqual(self.client.get(f"/forecast-runs/{run_id}/audit").status_code,200)
        self.assertGreaterEqual(len(self.client.get(f"/forecast-runs/{run_id}/events").json()),3)
        self.assertEqual(self.client.get("/forecast-runs/unknown").status_code,404)
        self.assertEqual(self.client.get("/forecast-runs/unknown/forecast").status_code,404)

    def test_request_rejects_extra_fields_and_paths(self):
        body = {"as_of":"2026-02-06T00:00:00Z","horizon":48,"model_id":"linear-v1",
                "weather_snapshot_id":"../../secret","path":"/tmp/file"}
        self.assertEqual(self.client.post("/forecast-runs",json=body).status_code,422)

    def test_recalculate_has_explicit_new_origin(self):
        body = {"as_of":"2026-02-06T00:00:00Z","horizon":48,"model_id":"linear-v1","weather_snapshot_id":"gfs-feb6"}
        parent = self.client.post("/forecast-runs",json=body).json()["id"]
        response = self.client.post(f"/forecast-runs/{parent}/recalculate",json={"weather_snapshot_id":"gfs-feb6","as_of":"2026-02-06T00:00:00Z"})
        self.assertEqual(response.status_code,202)
        self.assertEqual(response.json()["parent_run_id"],parent)
        self.assertFalse(response.json()["created"])
        self.assertEqual(response.json()["id"],parent)

    def test_committed_scada_model_runs_through_http_worker(self):
        shutil.copytree(SCADA, self.app.state.service.model_dir / "brev-scada-pooled-lgbm-20260201")
        body = {"as_of":"2026-02-06T00:00:00Z", "horizon":48,
                "model_id":"brev-scada-pooled-lgbm-20260201", "weather_snapshot_id":"gfs-feb6"}
        response = self.client.post("/forecast-runs", json=body)
        self.assertEqual(response.status_code, 202)
        run_id = response.json()["id"]
        self.app.state.service.process_one()
        forecast = self.client.get(f"/forecast-runs/{run_id}/forecast").json()
        self.assertEqual(forecast["status"], "SUCCEEDED")
        self.assertEqual(len(forecast["points"]), 96)
        self.assertIn("operational forecast accuracy is unmeasured", forecast["warnings"][0])
        audit = self.client.get(f"/forecast-runs/{run_id}/audit").json()["details"]
        self.assertEqual(audit["model_kind"], "scada_lightgbm_v1")


if __name__ == "__main__": unittest.main()

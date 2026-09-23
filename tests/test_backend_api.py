import json
import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.forecasting.adapter import FEATURES


FIXTURE = Path(__file__).parents[1] / "data/fixtures/noaa-gfs-20260206T000000Z-h48.json"


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


if __name__ == "__main__": unittest.main()

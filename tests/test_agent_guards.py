import json
import os
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.agent.operator import ForecastOperator, OpenAIResponses, OpenAIUnavailable
from backend.agent.tools import AgentTools, RunContext, tool_schemas
from backend.forecasting.adapter import FEATURES
from backend.replay import parse_timestamp
from backend.storage import Store
from backend.workflow import ForecastService


FIXTURE = Path(__file__).parents[1] / "data/fixtures/noaa-gfs-20260206T000000Z-h48.json"
RECORDED = Path(__file__).parent / "fixtures/agent-live-smoke-recorded.json"
RECORDED_LUNA = Path(__file__).parent / "fixtures/agent-luna-live-smoke-recorded.json"


class FakeResponses:
    def __init__(self, names):
        self.names = iter(names)
        self.calls = 0

    def respond(self, input_items, schemas, timeout):
        self.calls += 1
        try:
            name, args = next(self.names)
        except StopIteration:
            return {"output": [{"type": "message", "content": []}]}
        return {"id": f"response-{self.calls}",
                "output": [{"type": "function_call", "name": name,
                            "arguments": json.dumps(args), "call_id": f"call-{self.calls}"}]}


class FailingResponses:
    def respond(self, *_args):
        raise OpenAIUnavailable("simulated OpenAI outage")


class AgentGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.root = root
        snapshots, models = root / "snapshots", root / "models"
        snapshots.mkdir(); models.mkdir()
        shutil.copyfile(FIXTURE, snapshots / "fresh.json")
        shutil.copyfile(FIXTURE, snapshots / "previous.json")
        (models / "linear-v1.json").write_text(json.dumps({
            "model_id": "linear-v1", "training_cutoff": "2026-01-31T00:00:00Z",
            "feature_order": list(FEATURES), "intercept": 0.1,
            "coefficients": {name: (0.01 if name == "wind_speed_100m" else 0)
                             for name in FEATURES},
        }))
        self.service = ForecastService(Store(root / "runs.sqlite3"), snapshots, models)
        self.context = RunContext("2026-02-06T00:00:00Z", 48, "linear-v1",
                                  ("fresh", "previous"))

    def tearDown(self):
        self.temp.cleanup()

    def sequence(self, snapshot="fresh"):
        return [("get_weather_forecast", {"snapshot_id": snapshot}),
                ("validate_inputs", {}), ("run_forecast", {"model_id": "linear-v1"}),
                ("validate_forecast", {}), ("publish_forecast", {})]

    def test_live_responses_calls_are_real_items_and_engine_owns_numbers(self):
        fake = FakeResponses(self.sequence())
        result = ForecastOperator(self.service, self.context, fake).run_live(allow_fallback=False)
        self.assertEqual(result.execution, "live_openai")
        self.assertTrue(result.published)
        self.assertEqual(fake.calls, 5)
        self.assertEqual(len(result.forecast["points"]), 96)
        self.assertEqual([e["tool"] for e in result.trace if e["type"] == "AGENT_TOOL_CALL"],
                         [n for n, _ in self.sequence()])
        self.assertEqual(result.trace[0]["call_id"], "call-1")
        self.assertEqual(result.trace[0]["response_id"], "response-1")
        self.assertTrue(all("normalized_power" not in json.dumps(e) for e in result.trace))
        events = self.service.get_events(result.run_id)
        self.assertEqual(len([e for e in events if e["type"] == "AGENT_TOOL_CALL"]), 5)

    def test_existing_events_api_exposes_operator_calls(self):
        result = ForecastOperator(self.service, self.context,
                                  FakeResponses(self.sequence())).run_live(allow_fallback=False)
        app = create_app(database=self.root / "runs.sqlite3",
                         snapshot_dir=self.service.snapshot_dir,
                         model_dir=self.service.model_dir, background=False)
        events = TestClient(app).get(f"/forecast-runs/{result.run_id}/events")
        self.assertEqual(events.status_code, 200)
        calls = [event for event in events.json() if event["type"] == "AGENT_TOOL_CALL"]
        self.assertEqual(len(calls), 5)
        self.assertEqual(calls[0]["payload"]["call_id"], "call-1")
        self.assertEqual(calls[-1]["payload"]["tool"], "publish_forecast")

    def test_recorded_recovery_is_explicit_and_uses_previous_registered_snapshot(self):
        calls = [("get_weather_forecast", {"snapshot_id": "fresh"})] + self.sequence("previous")
        result = ForecastOperator(self.service, self.context, unavailable=frozenset({"fresh"})).run_recorded(calls)
        self.assertEqual(result.execution, "recorded")
        self.assertTrue(result.published)
        self.assertEqual(self.service.get_run(result.run_id)["weather_snapshot_id"], "previous")
        self.assertIn("injected demo failure", result.trace[0]["outcome"]["reason"])

    def test_saved_live_trace_replays_offline_as_recorded(self):
        shutil.copyfile(FIXTURE, self.service.snapshot_dir / "gfs-20260205-18z.json")
        bundle = json.loads((self.service.model_dir / "linear-v1.json").read_text())
        bundle["model_id"] = "service-fixture-linear-v1"
        (self.service.model_dir / "service-fixture-linear-v1.json").write_text(json.dumps(bundle))
        context = RunContext(self.context.as_of, 48, "service-fixture-linear-v1",
                             ("gfs-fresh-unavailable", "gfs-20260205-18z"))
        for path in (RECORDED, RECORDED_LUNA):
            with self.subTest(path=path.name):
                source = json.loads(path.read_text())
                self.assertTrue(source["recorded"])
                self.assertEqual(source["source_execution"], "live_openai")
                calls = [(entry["tool"], entry["arguments"]) for entry in source["trace"]]
                result = ForecastOperator(self.service, context,
                                          unavailable=frozenset({"gfs-fresh-unavailable"})).run_recorded(calls)
                self.assertEqual(result.execution, "recorded")
                self.assertTrue(result.published)

    def test_critical_temporal_failure_cannot_be_overridden_by_model(self):
        snapshot = json.loads(FIXTURE.read_text())
        snapshot["available_at"] = "2026-02-07T00:00:00Z"
        (self.service.snapshot_dir / "fresh.json").write_text(json.dumps(snapshot))
        tools = AgentTools(self.service, self.context)
        self.assertFalse(tools.invoke("get_weather_forecast", {"snapshot_id": "fresh"})["ok"])
        self.assertFalse(tools.invoke("publish_forecast", {})["ok"])
        self.assertIsNone(tools.run_id)

    def test_future_model_cutoff_blocks_input_validation(self):
        path = self.service.model_dir / "linear-v1.json"
        bundle = json.loads(path.read_text())
        bundle["training_cutoff"] = "2026-02-07T00:00:00Z"
        path.write_text(json.dumps(bundle))
        tools = AgentTools(self.service, self.context)
        self.assertTrue(tools.invoke("get_weather_forecast", {"snapshot_id": "fresh"})["ok"])
        self.assertFalse(tools.invoke("validate_inputs", {})["ok"])
        self.assertFalse(tools.invoke("run_forecast", {"model_id": "linear-v1"})["ok"])
        self.assertFalse(tools.invoke("publish_forecast", {})["ok"])

    def test_context_and_unknown_artifact_cannot_be_changed(self):
        tools = AgentTools(self.service, self.context)
        self.assertFalse(tools.invoke("get_weather_forecast", {"snapshot_id": "../secret"})["ok"])
        self.assertFalse(tools.invoke("get_weather_forecast", {"snapshot_id": "fresh", "as_of": "2026-02-07T00:00:00Z"})["ok"])
        self.assertFalse(tools.invoke("run_forecast", {"model_id": "evil"})["ok"])
        self.assertFalse(tools.invoke("publish_forecast", {"artifact_id": "unknown"})["ok"])
        self.assertFalse(tools.invoke("publish_forecast", {})["ok"])

    def test_gate_rechecks_snapshot_after_inference(self):
        tools = AgentTools(self.service, self.context)
        for name, args in self.sequence()[:3]:
            self.assertTrue(tools.invoke(name, args)["ok"])
        path = self.service.snapshot_dir / "fresh.json"
        path.write_text(path.read_text() + " ")
        self.assertFalse(tools.invoke("validate_forecast", {})["ok"])
        self.assertFalse(tools.invoke("publish_forecast", {})["ok"])

    def test_gate_rejects_changed_model_bundle_after_inference(self):
        tools = AgentTools(self.service, self.context)
        for name, args in self.sequence()[:3]:
            self.assertTrue(tools.invoke(name, args)["ok"])
        model_path = self.service.model_dir / "linear-v1.json"
        model_path.write_text(model_path.read_text() + " ")
        self.assertFalse(tools.invoke("validate_forecast", {})["ok"])
        self.assertFalse(tools.invoke("publish_forecast", {})["ok"])

    def test_openai_outage_has_separately_labelled_fallback(self):
        result = ForecastOperator(self.service, self.context, FailingResponses(),
                                  unavailable=frozenset({"fresh"})).run_live()
        self.assertEqual(result.execution, "deterministic_fallback")
        self.assertTrue(result.published)
        self.assertEqual(self.service.get_run(result.run_id)["weather_snapshot_id"], "previous")
        self.assertIn("simulated OpenAI outage", result.reason)

    def test_tool_budget_stops_live_sequence(self):
        result = ForecastOperator(self.service, self.context, FakeResponses(self.sequence()),
                                  max_tool_calls=2).run_live(allow_fallback=False)
        self.assertFalse(result.published)
        self.assertEqual(len([e for e in result.trace if e["type"] == "AGENT_TOOL_CALL"]), 2)

    def test_fallback_also_obeys_total_tool_budget(self):
        result = ForecastOperator(self.service, self.context, FailingResponses(),
                                  max_tool_calls=2).run_live()
        self.assertEqual(result.execution, "deterministic_fallback")
        self.assertFalse(result.published)
        self.assertLessEqual(len([e for e in result.trace if e["type"] == "AGENT_TOOL_CALL"]), 2)

    def test_all_schemas_are_strict_and_do_not_accept_cutoffs_or_values(self):
        schemas = tool_schemas(self.context.candidates, self.context.model_id)
        self.assertEqual(len(schemas), 6)
        for schema in schemas:
            self.assertTrue(schema["strict"])
            parameters = schema["parameters"]
            self.assertFalse(parameters["additionalProperties"])
            self.assertEqual(set(parameters["required"]), set(parameters["properties"]))
            self.assertNotIn("as_of", parameters["properties"])
            self.assertNotIn("normalized_power", parameters["properties"])

    def test_luna_default_reads_local_env_without_exposing_key(self):
        env_file = self.root / ".env"
        env_file.write_text("OPENAI_API_KEY=example-local-secret\n")
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            client = OpenAIResponses(env_file=env_file)
            self.assertEqual(client.model, "gpt-6-luna")
            self.assertEqual(client._api_key(), "example-local-secret")

    def test_recalculation_uses_only_server_approved_new_origin(self):
        snapshot = json.loads(FIXTURE.read_text())
        for point in snapshot["points"]:
            for field in ("target_start", "target_end"):
                point[field] = (parse_timestamp(point[field]) + timedelta(days=1)).isoformat()
        (self.service.snapshot_dir / "next.json").write_text(json.dumps(snapshot))
        context = RunContext(self.context.as_of, 48, "linear-v1", ("fresh",),
                             recalculation_snapshot_id="next",
                             recalculation_as_of="2026-02-07T00:00:00Z")
        tools = AgentTools(self.service, context)
        for name, args in self.sequence()[:3]:
            self.assertTrue(tools.invoke(name, args)["ok"])
        result = tools.invoke("request_recalculation", {"reason": "new snapshot available"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "QUEUED")
        child = self.service.get_run(result["child_run_id"])
        self.assertEqual(child["parent_run_id"], tools.run_id)
        self.assertEqual(child["as_of"], "2026-02-07T00:00:00+00:00")
        self.assertFalse(tools.invoke("request_recalculation", {"reason": "x", "as_of": "2026-02-08T00:00:00Z"})["ok"])


if __name__ == "__main__":
    unittest.main()

"""Server-owned tool schemas and deterministic forecast publication gate."""

from __future__ import annotations

import json
import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.forecasting import load_bundle
from backend.replay import ReplayContext, parse_timestamp
from backend.workflow import ForecastService


def _schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def tool_schemas(candidates: tuple[str, ...], model_id: str) -> list[dict[str, Any]]:
    definitions = (
        ("get_weather_forecast", "Try a server-approved weather candidate for this fixed run.",
         {"snapshot_id": {"type": "string", "enum": list(candidates)}}),
        ("validate_inputs", "Validate the selected snapshot and fixed model cutoffs.", {}),
        ("run_forecast", "Run the server's numerical forecasting engine.",
         {"model_id": {"type": "string", "enum": [model_id]}}),
        ("validate_forecast", "Check the numerical artifact without revealing its values.", {}),
        ("publish_forecast", "Apply the mandatory server-side publication gate.", {}),
        ("request_recalculation", "Queue a server-approved immutable child run.",
         {"reason": {"type": "string", "maxLength": 240}}),
    )
    return [{"type": "function", "name": name, "description": description,
             "parameters": _schema(properties), "strict": True}
            for name, description, properties in definitions]


@dataclass(frozen=True)
class RunContext:
    """All cutoff and source choices are supplied by the application, not the LLM."""

    as_of: str
    horizon: int
    model_id: str
    candidates: tuple[str, ...]
    mode: str = "replay"
    recalculation_snapshot_id: str | None = None
    recalculation_as_of: str | None = None

    def __post_init__(self) -> None:
        replay = ReplayContext(parse_timestamp(self.as_of), self.horizon)
        if not self.candidates or len(set(self.candidates)) != len(self.candidates):
            raise ValueError("weather candidates must be nonempty and unique")
        if self.mode not in ("replay", "live"):
            raise ValueError("invalid run mode")
        if (self.recalculation_snapshot_id is None) != (self.recalculation_as_of is None):
            raise ValueError("recalculation target requires both snapshot and origin")
        if self.recalculation_as_of is not None:
            target = ReplayContext(parse_timestamp(self.recalculation_as_of), self.horizon)
            if target.as_of <= replay.as_of:
                raise ValueError("recalculation origin must be later than parent origin")
            object.__setattr__(self, "recalculation_as_of", target.as_of.isoformat())
        object.__setattr__(self, "as_of", replay.as_of.isoformat())
        object.__setattr__(self, "candidates", tuple(self.candidates))

    @property
    def replay(self) -> ReplayContext:
        return ReplayContext(parse_timestamp(self.as_of), self.horizon)


class PublishRejected(RuntimeError):
    pass


class AgentTools:
    def __init__(self, service: ForecastService, context: RunContext,
                 unavailable: frozenset[str] = frozenset()):
        self.service = service
        self.context = context
        self.unavailable = unavailable
        self.selected: str | None = None
        self.snapshot_hash: str | None = None
        self.model_hash: str | None = None
        self.run_id: str | None = None
        self.input_passed = False
        self.output_passed = False
        self.published = False
        self.trace: list[dict[str, Any]] = []
        self._persisted = 0

    def _record(self, name: str, arguments: dict[str, Any], outcome: dict[str, Any]) -> None:
        entry = {"type": "AGENT_TOOL_CALL", "tool": name, "arguments": arguments,
                 "outcome": outcome, "created_at": datetime.now(timezone.utc).isoformat()}
        self.trace.append(entry)
        self._flush()

    def _flush(self) -> None:
        if self.run_id is None:
            return
        pending = self.trace[self._persisted:]
        if not pending:
            return
        with self.service.store.tx(immediate=True) as db:
            db.executemany("INSERT INTO events(run_id,created_at,type,payload_json) VALUES(?,?,?,?)",
                           [(self.run_id, e["created_at"], e["type"],
                             json.dumps({k: v for k, v in e.items() if k not in ("type", "created_at")},
                                        sort_keys=True)) for e in pending])
        self._persisted = len(self.trace)

    def decision(self, reason: str, execution: str) -> None:
        self.trace.append({"type": "AGENT_DECISION", "reason": reason[:500],
                           "execution": execution,
                           "created_at": datetime.now(timezone.utc).isoformat()})
        self._flush()

    def invoke(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        allowed = {"get_weather_forecast": {"snapshot_id"}, "validate_inputs": set(),
                   "run_forecast": {"model_id"}, "validate_forecast": set(),
                   "publish_forecast": set(), "request_recalculation": {"reason"}}
        if name not in allowed or set(args) != allowed[name]:
            outcome = {"ok": False, "reason": "invalid tool or arguments"}
            self._record(name, {}, outcome)
            return outcome
        try:
            outcome = getattr(self, name)(**args)
        except (ValueError, RuntimeError, KeyError, TypeError) as exc:
            outcome = {"ok": False, "reason": str(exc)[:300]}
        self._record(name, args, outcome)
        return outcome

    def get_weather_forecast(self, snapshot_id: str) -> dict[str, Any]:
        if self.run_id is not None:
            raise PublishRejected("weather cannot change after numerical inference")
        if snapshot_id not in self.context.candidates:
            raise PublishRejected("snapshot is not an approved candidate")
        if snapshot_id in self.unavailable:
            raise PublishRejected("weather source unavailable (injected demo failure)")
        _, digest = self.service.load_snapshot(snapshot_id, self.context.replay)
        self.selected = snapshot_id
        self.snapshot_hash = digest
        self.input_passed = False
        return {"ok": True, "snapshot_id": snapshot_id, "snapshot_hash": digest,
                "as_of": self.context.as_of}

    def validate_inputs(self) -> dict[str, Any]:
        if self.selected is None:
            raise PublishRejected("select weather first")
        _, digest = self.service.load_snapshot(self.selected, self.context.replay)
        if digest != self.snapshot_hash:
            raise PublishRejected("registered snapshot changed")
        model_path = self.service._registered(self.service.model_dir, self.context.model_id, "model_id")
        load_bundle(model_path, self.context.model_id, self.context.replay)
        self.model_hash = hashlib.sha256(model_path.read_bytes()).hexdigest()
        self.input_passed = True
        return {"ok": True, "temporal": "PASS", "data": "PASS",
                "snapshot_id": self.selected, "model_id": self.context.model_id}

    def run_forecast(self, model_id: str) -> dict[str, Any]:
        if model_id != self.context.model_id:
            raise PublishRejected("model_id is immutable")
        if not self.input_passed or self.selected is None:
            raise PublishRejected("input validation has not passed")
        run, _ = self.service.create_run(as_of=self.context.as_of, horizon=self.context.horizon,
                                         model_id=model_id, weather_snapshot_id=self.selected,
                                         mode=self.context.mode)
        self.run_id = run["id"]
        self._flush()
        if run["status"] == "QUEUED":
            self.service.process_one()
        current = self.service.get_run(self.run_id)
        if current is None or current["status"] != "SUCCEEDED":
            raise PublishRejected("forecast engine blocked: " + str(current and current["error"]))
        return {"ok": True, "artifact_id": self.run_id, "status": "COMPUTED"}

    def validate_forecast(self) -> dict[str, Any]:
        self._gate()
        self.output_passed = True
        return {"ok": True, "artifact_id": self.run_id, "forecast": "PASS"}

    def publish_forecast(self) -> dict[str, Any]:
        self._gate()  # repeat independently even if the model skips validation
        if not self.input_passed or not self.output_passed:
            raise PublishRejected("required validation tools have not passed")
        self.published = True
        return {"ok": True, "artifact_id": self.run_id, "publication": "APPROVED"}

    def request_recalculation(self, reason: str) -> dict[str, Any]:
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 240:
            raise PublishRejected("recalculation reason must be 1–240 characters")
        if self.run_id is None:
            raise PublishRejected("a parent run is required for recalculation")
        if self.context.recalculation_snapshot_id is None or self.context.recalculation_as_of is None:
            raise PublishRejected("no server-approved recalculation target")
        child, created = self.service.recalculate(
            self.run_id, self.context.recalculation_snapshot_id,
            self.context.recalculation_as_of)
        return {"ok": True, "status": "QUEUED" if created else "EXISTING",
                "child_run_id": child["id"], "parent_run_id": self.run_id,
                "reason": reason.strip(), "as_of": child["as_of"]}

    def _gate(self) -> None:
        if self.run_id is None or self.selected is None:
            raise PublishRejected("unknown forecast artifact")
        run = self.service.get_run(self.run_id)
        if run is None or run["status"] != "SUCCEEDED":
            raise PublishRejected("forecast artifact is not successful")
        if (run["as_of"] != self.context.as_of or run["horizon"] != self.context.horizon
                or run["model_id"] != self.context.model_id
                or run["weather_snapshot_id"] != self.selected
                or run["snapshot_hash"] != self.snapshot_hash):
            raise PublishRejected("forecast artifact context mismatch")
        snapshot, digest = self.service.load_snapshot(self.selected, self.context.replay)
        if digest != run["snapshot_hash"]:
            raise PublishRejected("registered snapshot changed after inference")
        model_path = self.service._registered(self.service.model_dir, self.context.model_id, "model_id")
        load_bundle(model_path, self.context.model_id, self.context.replay)
        if self.model_hash is None or hashlib.sha256(model_path.read_bytes()).hexdigest() != self.model_hash:
            raise PublishRejected("registered model changed after input validation")
        audit = self.service.get_audit(self.run_id)
        if audit is None or audit["status"] != "SUCCEEDED" or audit["details"].get("point_count") != 2 * self.context.horizon:
            raise PublishRejected("forecast audit is incomplete")
        forecast = self.service.get_forecast(self.run_id)
        points = forecast["points"] if forecast else []
        expected = {(p["turbine_id"], parse_timestamp(p["target_start"]).isoformat(),
                     parse_timestamp(p["target_end"]).isoformat()) for p in snapshot["points"]}
        actual = {(p["turbine_id"], parse_timestamp(p["target_start"]).isoformat(),
                   parse_timestamp(p["target_end"]).isoformat()) for p in points}
        if len(points) != 2 * self.context.horizon or len(expected) != len(points) or actual != expected:
            raise PublishRejected("forecast coverage is incomplete")
        if any(not isinstance(p["normalized_power"], (int, float))
               or not math.isfinite(p["normalized_power"])
               or not 0 <= p["normalized_power"] <= 1 for p in points):
            raise PublishRejected("forecast power is invalid")

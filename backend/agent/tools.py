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
                 unavailable: frozenset[str] = frozenset(), run_id: str | None = None,
                 worker_attempt: int | None = None):
        self.service = service
        self.context = context
        self.unavailable = unavailable
        self.selected: str | None = None
        self.snapshot_hash: str | None = None
        self.model_hash: str | None = None
        self.run_id: str | None = run_id
        self.worker_attempt = worker_attempt
        self.input_passed = False
        self.output_passed = False
        self.published = False
        self.trace: list[dict[str, Any]] = []
        self._persisted = 0

    def _record(self, name: str, arguments: dict[str, Any], outcome: dict[str, Any],
                call_id: str | None = None, response_id: str | None = None) -> None:
        entry = {"type": "AGENT_TOOL_CALL", "tool": name, "arguments": arguments,
                 "outcome": outcome, "created_at": datetime.now(timezone.utc).isoformat()}
        if call_id is not None:
            entry["call_id"] = call_id
        if response_id is not None:
            entry["response_id"] = response_id
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

    def invoke(self, name: str, args: dict[str, Any], *, call_id: str | None = None,
               response_id: str | None = None) -> dict[str, Any]:
        allowed = {"get_weather_forecast": {"snapshot_id"}, "validate_inputs": set(),
                   "run_forecast": {"model_id"}, "validate_forecast": set(),
                   "publish_forecast": set(), "request_recalculation": {"reason"}}
        if name not in allowed or set(args) != allowed[name]:
            outcome = {"ok": False, "reason": "invalid tool or arguments"}
            self._record(name, {}, outcome, call_id, response_id)
            return outcome
        try:
            outcome = getattr(self, name)(**args)
        except (ValueError, RuntimeError, KeyError, TypeError) as exc:
            outcome = {"ok": False, "reason": str(exc)[:300]}
        self._record(name, args, outcome, call_id, response_id)
        return outcome

    def get_weather_forecast(self, snapshot_id: str) -> dict[str, Any]:
        if self.run_id is not None:
            existing = self.service.get_run(self.run_id)
            if existing is not None and existing["status"] == "COMPUTED" and snapshot_id != existing["weather_snapshot_id"]:
                raise PublishRejected("weather cannot change after numerical inference")
        if snapshot_id not in self.context.candidates:
            raise PublishRejected("snapshot is not an approved candidate")
        if snapshot_id in self.unavailable:
            raise PublishRejected("weather source unavailable (injected demo failure)")
        _, digest = self.service.load_snapshot(snapshot_id, self.context.replay)
        if self.run_id is not None:
            existing = self.service.get_run(self.run_id)
            if existing is not None and existing["status"] == "COMPUTED" and digest != existing["snapshot_hash"]:
                raise PublishRejected("weather changed after numerical inference")
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
        model_path = self.service._registered_model(self.context.model_id)
        bundle = load_bundle(model_path, self.context.model_id, self.context.replay)
        self.model_hash = self.service.model_fingerprint(model_path)
        if self.model_hash is None:
            raise PublishRejected("model fingerprint is unavailable")
        self.input_passed = True
        return {"ok": True, "temporal": "PASS", "data": "PASS",
                "snapshot_id": self.selected, "model_id": self.context.model_id}

    def run_forecast(self, model_id: str) -> dict[str, Any]:
        if model_id != self.context.model_id:
            raise PublishRejected("model_id is immutable")
        if not self.input_passed or self.selected is None:
            raise PublishRejected("input validation has not passed")
        if self.run_id is None:
            run, _ = self.service.create_run(as_of=self.context.as_of, horizon=self.context.horizon,
                                             model_id=model_id, weather_snapshot_id=self.selected,
                                             mode=self.context.mode)
            self.run_id = run["id"]
        self._flush()
        current = self.service.get_run(self.run_id)
        if current is None:
            raise PublishRejected("forecast artifact disappeared")
        if current["status"] not in ("COMPUTED", "SUCCEEDED"):
            self.service.compute_run(self.run_id, self.worker_attempt)
        current = self.service.get_run(self.run_id)
        if current is None or current["status"] not in ("COMPUTED", "SUCCEEDED"):
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
        audit = self.service.get_audit(self.run_id)
        self.service.store.publish(self.run_id, audit["details"])
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
        if run is None or run["status"] not in ("COMPUTED", "SUCCEEDED"):
            raise PublishRejected("forecast artifact is not successful")
        if (run["as_of"] != self.context.as_of or run["horizon"] != self.context.horizon
                or run["model_id"] != self.context.model_id
                or run["weather_snapshot_id"] != self.selected
                or run["snapshot_hash"] != self.snapshot_hash):
            raise PublishRejected("forecast artifact context mismatch")
        snapshot, digest = self.service.load_snapshot(self.selected, self.context.replay)
        if digest != run["snapshot_hash"]:
            raise PublishRejected("registered snapshot changed after inference")
        model_path = self.service._registered_model(self.context.model_id)
        bundle = load_bundle(model_path, self.context.model_id, self.context.replay)
        current_hash = self.service.model_fingerprint(model_path)
        if self.model_hash is None or current_hash != self.model_hash:
            raise PublishRejected("registered model changed after input validation")
        audit = self.service.get_audit(self.run_id)
        if audit is None or audit["status"] not in ("COMPUTED", "SUCCEEDED") or audit["details"].get("point_count") != 2 * self.context.horizon:
            raise PublishRejected("forecast audit is incomplete")
        if audit["details"].get("model_bundle_sha256") != self.model_hash:
            raise PublishRejected("forecast model differs from computed artifact")
        points = self.service.store.points(self.run_id)
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

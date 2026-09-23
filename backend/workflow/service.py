"""Application service shared by HTTP routes, worker, scheduler and agent tools."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.forecasting import ModelBlocked, load_bundle, predict
from backend.replay import ReplayContext, parse_timestamp, validate_weather_snapshot
from backend.storage import Store

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class RegistryError(ValueError):
    pass


class ForecastService:
    def __init__(self, store: Store, snapshot_dir: str | Path, model_dir: str | Path):
        self.store = store
        self.snapshot_dir = Path(snapshot_dir).resolve()
        self.model_dir = Path(model_dir).resolve()
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.model_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _id(value: str, label: str) -> str:
        if not SAFE_ID.fullmatch(value):
            raise RegistryError(f"invalid {label}")
        return value

    def _registered(self, root: Path, identifier: str, label: str) -> Path:
        identifier = self._id(identifier, label)
        path = (root / f"{identifier}.json").resolve()
        if path.parent != root:
            raise RegistryError(f"invalid {label}")
        return path

    def load_snapshot(self, snapshot_id: str, context: ReplayContext) -> tuple[dict[str, Any], str]:
        path = self._registered(self.snapshot_dir, snapshot_id, "weather_snapshot_id")
        if not path.is_file():
            raise RegistryError(f"weather snapshot is not registered: {snapshot_id}")
        raw = path.read_bytes()
        try:
            snapshot = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RegistryError(f"weather snapshot is invalid: {snapshot_id}") from exc
        validate_weather_snapshot(snapshot, context)
        return snapshot, hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _signature(as_of: str, horizon: int, model_id: str, snapshot_hash: str) -> str:
        return hashlib.sha256(
            json.dumps([as_of,horizon,model_id,snapshot_hash],separators=(",", ":")).encode()
        ).hexdigest()

    def create_run(
        self, *, as_of: str, horizon: int, model_id: str,
        weather_snapshot_id: str, mode: str = "replay",
        parent_run_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if mode not in ("replay", "live"):
            raise ValueError("mode must be replay or live")
        context = ReplayContext(parse_timestamp(as_of), horizon)
        if mode == "live":
            current_hour = datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)
            if context.as_of != current_hour:
                raise ValueError("live as_of must equal the current UTC hour")
        self._id(model_id, "model_id")
        _, snapshot_hash = self.load_snapshot(weather_snapshot_id, context)
        canonical_as_of = context.as_of.isoformat()
        input_hash = self._signature(canonical_as_of,horizon,model_id,snapshot_hash)
        key = f"revision:{parent_run_id}:{input_hash}" if parent_run_id else f"root:{input_hash}:{mode}"
        return self.store.create_run({
            "mode":mode,"as_of":canonical_as_of,"horizon":horizon,"model_id":model_id,
            "weather_snapshot_id":weather_snapshot_id,"snapshot_hash":snapshot_hash,
            "input_hash":input_hash,"idempotency_key":key,"parent_run_id":parent_run_id,
        })

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.store.get_run(run_id)

    def get_forecast(self, run_id: str) -> dict[str, Any] | None:
        run = self.store.get_run(run_id)
        if not run:
            return None
        return {"run_id":run_id,"status":run["status"],"points":self.store.points(run_id) if run["status"] == "SUCCEEDED" and run.get("published") else [],
                "warnings":[run["warning"]] if run["warning"] else [],"error":run["error"],
                "provenance":{"as_of":run["as_of"],"horizon":run["horizon"],"model_id":run["model_id"],
                              "weather_snapshot_id":run["weather_snapshot_id"],"snapshot_hash":run["snapshot_hash"]}}

    def get_audit(self, run_id: str) -> dict[str, Any] | None:
        run = self.store.get_run(run_id)
        if not run:
            return None
        return {"run_id":run_id,"status":run["status"],"as_of":run["as_of"],"horizon":run["horizon"],
                "model_id":run["model_id"],"weather_snapshot_id":run["weather_snapshot_id"],
                "snapshot_hash":run["snapshot_hash"],"parent_run_id":run["parent_run_id"],
                "details":json.loads(run["audit_json"]),"error":run["error"]}

    def get_events(self, run_id: str) -> list[dict[str, Any]] | None:
        if not self.store.get_run(run_id):
            return None
        return [{**e,"payload":json.loads(e.pop("payload_json"))} for e in self.store.events(run_id)]

    def recalculate(self, run_id: str, weather_snapshot_id: str, as_of: str | None = None) -> tuple[dict[str, Any], bool]:
        parent = self.store.get_run(run_id)
        if not parent:
            raise KeyError(run_id)
        revision_as_of = as_of or parent["as_of"]
        context = ReplayContext(parse_timestamp(revision_as_of),parent["horizon"])
        _, snapshot_hash = self.load_snapshot(weather_snapshot_id,context)
        signature = self._signature(context.as_of.isoformat(),parent["horizon"],parent["model_id"],snapshot_hash)
        if signature == parent["input_hash"]:
            return parent, False
        return self.create_run(as_of=revision_as_of,horizon=parent["horizon"],model_id=parent["model_id"],
                               weather_snapshot_id=weather_snapshot_id,mode=parent["mode"],parent_run_id=run_id)

    def compute_run(self, run_id: str, worker_attempt: int | None = None) -> str:
        run = self.store.get_run(run_id)
        if not run: raise KeyError(run_id)
        context = ReplayContext(parse_timestamp(run["as_of"]),run["horizon"])
        audit = {"input_hash":run["input_hash"],"snapshot_hash":run["snapshot_hash"],"worker_attempt":worker_attempt}
        try:
            snapshot, actual_hash = self.load_snapshot(run["weather_snapshot_id"],context)
            if actual_hash != run["snapshot_hash"]: raise ModelBlocked("registered snapshot changed after run creation")
            bundle = load_bundle(self._registered(self.model_dir,run["model_id"],"model_id"),run["model_id"],context)
            points = predict(bundle,snapshot["points"])
            expected = {(p["turbine_id"],parse_timestamp(p["target_start"]),parse_timestamp(p["target_end"])) for p in snapshot["points"]}; actual = {(p["turbine_id"],parse_timestamp(p["target_start"]),parse_timestamp(p["target_end"])) for p in points}
            if len(points) != 2 * run["horizon"] or actual != expected: raise ModelBlocked("model output has incomplete coverage")
            audit.update({"model_training_cutoff":bundle["training_cutoff"],"point_count":len(points)}); self.store.compute(run_id,points,audit)
        except Exception as exc: self.store.block(run_id,str(exc),audit)
        return run_id

    def process_one(self, lease_seconds: int = 60) -> str | None:
        job = self.store.claim_job(lease_seconds)
        if not job:
            return None
        run = self.store.get_run(job["run_id"])
        assert run is not None
        from backend.agent.operator import ForecastOperator
        from backend.agent.tools import RunContext
        context = RunContext(run["as_of"], run["horizon"], run["model_id"], (run["weather_snapshot_id"],), run["mode"])
        result = ForecastOperator(self, context, run_id=run["id"], worker_attempt=job["attempts"]).run_live(allow_fallback=False)
        if not result.published:
            audit = self.get_audit(run["id"])
            self.store.block(run["id"], result.reason, audit["details"] if audit else {})
        return run["id"]

    def tick_scheduler(self) -> int:
        """Create eligible different-origin revisions from registered snapshots."""
        created = 0
        runs = list(self.store.list_runs())
        for parent in runs:
            if parent["parent_run_id"] is not None:
                continue
            for path in sorted(self.snapshot_dir.glob("*.json")):
                snapshot_id = path.stem
                try:
                    snapshot = json.loads(path.read_text(encoding="utf-8"))
                    origin = parse_timestamp(snapshot["points"][0]["target_start"])
                    if origin <= parse_timestamp(parent["as_of"]):
                        continue
                    if parent["mode"] == "live":
                        now_hour = datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)
                        if origin != now_hour:
                            continue
                    _, was_created = self.recalculate(parent["id"],snapshot_id,origin.isoformat())
                    created += int(was_created)
                except (KeyError, OSError, TypeError, ValueError, RegistryError):
                    continue
        return created

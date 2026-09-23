"""SQLite persistence for immutable forecast runs and durable jobs."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    @contextmanager
    def tx(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self) -> None:
        with closing(self.connect()) as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
              id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              status TEXT NOT NULL, mode TEXT NOT NULL, as_of TEXT NOT NULL,
              horizon INTEGER NOT NULL CHECK(horizon IN (24,48)), model_id TEXT NOT NULL,
              weather_snapshot_id TEXT NOT NULL, snapshot_hash TEXT NOT NULL,
              input_hash TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
              parent_run_id TEXT REFERENCES runs(id), warning TEXT, error TEXT,
              audit_json TEXT NOT NULL DEFAULT '{}', published INTEGER NOT NULL DEFAULT 0
            );
            CREATE UNIQUE INDEX IF NOT EXISTS revisions_unique
              ON runs(parent_run_id,input_hash) WHERE parent_run_id IS NOT NULL;
            CREATE TABLE IF NOT EXISTS points (
              run_id TEXT NOT NULL REFERENCES runs(id), turbine_id TEXT NOT NULL,
              target_start TEXT NOT NULL, target_end TEXT NOT NULL,
              normalized_power REAL NOT NULL CHECK(normalized_power>=0 AND normalized_power<=1),
              PRIMARY KEY(run_id,turbine_id,target_start)
            );
            CREATE TABLE IF NOT EXISTS events (
              seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
              created_at TEXT NOT NULL, type TEXT NOT NULL, payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
              status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
              lease_until TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              error TEXT
            );
            """)
            if "published" not in {row[1] for row in db.execute("PRAGMA table_info(runs)")}:
                db.execute("ALTER TABLE runs ADD COLUMN published INTEGER NOT NULL DEFAULT 0")

    def create_run(self, record: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        timestamp = now_utc()
        run_id = record.get("id") or str(uuid.uuid4())
        with self.tx(immediate=True) as db:
            existing = db.execute(
                "SELECT * FROM runs WHERE idempotency_key=?", (record["idempotency_key"],)
            ).fetchone()
            if existing:
                return dict(existing), False
            db.execute("""INSERT INTO runs
              (id,created_at,updated_at,status,mode,as_of,horizon,model_id,
               weather_snapshot_id,snapshot_hash,input_hash,idempotency_key,parent_run_id,audit_json)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                run_id,timestamp,timestamp,"QUEUED",record["mode"],record["as_of"],
                record["horizon"],record["model_id"],record["weather_snapshot_id"],
                record["snapshot_hash"],record["input_hash"],record["idempotency_key"],
                record.get("parent_run_id"),"{}",
            ))
            db.execute("INSERT INTO jobs(id,run_id,status,created_at,updated_at) VALUES(?,?,?,?,?)",
                       (str(uuid.uuid4()),run_id,"PENDING",timestamp,timestamp))
            db.execute("INSERT INTO events(run_id,created_at,type,payload_json) VALUES(?,?,?,?)",
                       (run_id,timestamp,"RUN_QUEUED",json.dumps({"parent_run_id":record.get("parent_run_id")})))
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            return dict(row), True

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            return dict(row) if row else None

    def list_runs(self) -> list[dict[str, Any]]:
        with closing(self.connect()) as db:
            return [dict(row) for row in db.execute("SELECT * FROM runs ORDER BY created_at")]

    def events(self, run_id: str) -> list[dict[str, Any]]:
        with closing(self.connect()) as db:
            return [dict(row) for row in db.execute(
                "SELECT seq,created_at,type,payload_json FROM events WHERE run_id=? ORDER BY seq", (run_id,)
            )]

    def points(self, run_id: str) -> list[dict[str, Any]]:
        with closing(self.connect()) as db:
            return [dict(row) for row in db.execute(
                "SELECT turbine_id,target_start,target_end,normalized_power FROM points WHERE run_id=? ORDER BY turbine_id,target_start",
                (run_id,),
            )]

    def claim_job(self, lease_seconds: int = 60) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        with self.tx(immediate=True) as db:
            row = db.execute("""SELECT * FROM jobs
                WHERE status='PENDING' OR (status='RUNNING' AND lease_until<?)
                ORDER BY created_at LIMIT 1""", (now.isoformat(),)).fetchone()
            if not row:
                return None
            lease = (now + timedelta(seconds=lease_seconds)).isoformat()
            db.execute("UPDATE jobs SET status='RUNNING',attempts=attempts+1,lease_until=?,updated_at=? WHERE id=?",
                       (lease,now.isoformat(),row["id"]))
            db.execute("UPDATE runs SET status='RUNNING',updated_at=? WHERE id=?", (now.isoformat(),row["run_id"]))
            db.execute("INSERT INTO events(run_id,created_at,type,payload_json) VALUES(?,?,?,?)",
                       (row["run_id"],now.isoformat(),"JOB_CLAIMED",json.dumps({"lease_until":lease})))
            return dict(db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())

    def compute(self, run_id: str, points: list[dict[str, Any]], audit: dict[str, Any]) -> None:
        timestamp = now_utc()
        with self.tx(immediate=True) as db:
            db.executemany("""INSERT INTO points(run_id,turbine_id,target_start,target_end,normalized_power)
                VALUES(?,?,?,?,?)""", [(run_id,p["turbine_id"],p["target_start"],p["target_end"],p["normalized_power"]) for p in points])
            db.execute("UPDATE runs SET status='COMPUTED',updated_at=?,audit_json=? WHERE id=?",
                       (timestamp,json.dumps(audit,sort_keys=True),run_id))
            db.execute("INSERT INTO events(run_id,created_at,type,payload_json) VALUES(?,?,?,?)",
                       (run_id,timestamp,"FORECAST_COMPUTED",json.dumps({"points":len(points)})))

    def publish(self, run_id: str, audit: dict[str, Any]) -> None:
        timestamp = now_utc()
        with self.tx(immediate=True) as db:
            row = db.execute("SELECT status,published FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row: raise ValueError("forecast is not computed")
            if row["published"]: return
            if row["status"] != "COMPUTED": raise ValueError("forecast is not computed")
            db.execute("UPDATE runs SET status='SUCCEEDED',published=1,updated_at=?,audit_json=? WHERE id=?", (timestamp,json.dumps(audit,sort_keys=True),run_id))
            db.execute("UPDATE jobs SET status='DONE',updated_at=?,lease_until=NULL WHERE run_id=?", (timestamp,run_id))
            db.execute("INSERT INTO events(run_id,created_at,type,payload_json) VALUES(?,?,?,?)", (run_id,timestamp,"FORECAST_READY",json.dumps({"publication":"APPROVED"})))

    def block(self, run_id: str, reason: str, audit: dict[str, Any]) -> None:
        timestamp = now_utc()
        with self.tx(immediate=True) as db:
            db.execute("UPDATE runs SET status='BLOCKED',error=?,updated_at=?,audit_json=? WHERE id=?",
                       (reason,timestamp,json.dumps(audit,sort_keys=True),run_id))
            db.execute("UPDATE jobs SET status='DONE',error=?,updated_at=?,lease_until=NULL WHERE run_id=?",
                       (reason,timestamp,run_id))
            db.execute("INSERT INTO events(run_id,created_at,type,payload_json) VALUES(?,?,?,?)",
                       (run_id,timestamp,"RUN_BLOCKED",json.dumps({"reason":reason})))

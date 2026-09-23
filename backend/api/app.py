"""FastAPI surface for forecast runs."""

from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from time import sleep

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from backend.storage import Store
from backend.workflow import ForecastService, RegistryError


class CreateRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    as_of: str
    horizon: int = Field(ge=24,le=48)
    model_id: str = Field(min_length=1,max_length=128)
    weather_snapshot_id: str = Field(min_length=1,max_length=128)
    mode: str = "replay"


class Recalculate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weather_snapshot_id: str = Field(min_length=1,max_length=128)
    as_of: str | None = None


def create_app(*, database: str | Path | None = None, snapshot_dir: str | Path | None = None,
               model_dir: str | Path | None = None, background: bool = True) -> FastAPI:
    data = Path(os.getenv("FORECAST_DATA_DIR", "data/runtime")).resolve()
    store = Store(database or data / "forecast.sqlite3")
    service = ForecastService(store,snapshot_dir or data / "snapshots",model_dir or data / "models")
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            service.tick_scheduler()
            while service.process_one() is not None:
                pass
            stop.wait(1.0)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        thread = threading.Thread(target=loop,name="forecast-worker",daemon=True) if background else None
        if thread:
            thread.start()
        yield
        stop.set()
        if thread:
            thread.join(timeout=3)

    api = FastAPI(title="Wind Forecast Replay API",version="0.1.0",lifespan=lifespan)
    api.state.service = service

    def require(run_id: str):
        value = service.get_run(run_id)
        if value is None:
            raise HTTPException(404,"forecast run not found")
        return value

    @api.get("/health")
    def health():
        return {"status":"ok","worker":"background" if background else "manual"}

    @api.post("/forecast-runs",status_code=status.HTTP_202_ACCEPTED)
    def create(body: CreateRun):
        try:
            run, created = service.create_run(**body.model_dump())
        except (RegistryError,TypeError,ValueError) as exc:
            raise HTTPException(422,str(exc)) from exc
        return {"id":run["id"],"status":run["status"],"created":created}

    @api.get("/forecast-runs/{run_id}")
    def get_run(run_id: str):
        return require(run_id)

    @api.get("/forecast-runs/{run_id}/forecast")
    def forecast(run_id: str):
        require(run_id)
        return service.get_forecast(run_id)

    @api.get("/forecast-runs/{run_id}/audit")
    def audit(run_id: str):
        require(run_id)
        return service.get_audit(run_id)

    @api.get("/forecast-runs/{run_id}/events")
    def events(run_id: str):
        require(run_id)
        return service.get_events(run_id)

    @api.post("/forecast-runs/{run_id}/recalculate",status_code=status.HTTP_202_ACCEPTED)
    def recalculate(run_id: str, body: Recalculate):
        require(run_id)
        try:
            run, created = service.recalculate(run_id,body.weather_snapshot_id,body.as_of)
        except (RegistryError,TypeError,ValueError) as exc:
            raise HTTPException(422,str(exc)) from exc
        return {"id":run["id"],"status":run["status"],"created":created,"parent_run_id":run_id}
    return api


app = create_app()

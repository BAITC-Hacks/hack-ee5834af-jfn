# Forecast backend API

The backend persists immutable forecast runs, jobs, events and points in SQLite. One in-process worker leases durable jobs and recovers expired leases after a restart. A scheduler scans the registered snapshot inbox and creates one deduplicated child revision for each eligible new-origin snapshot.

## Run locally

Use Python 3.11–3.13:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-backend.txt
FORECAST_DATA_DIR=data/runtime .venv/bin/uvicorn backend.api.app:app
```

Register inputs by placing JSON files under the server-controlled directories:

- `data/runtime/snapshots/<weather_snapshot_id>.json`
- `data/runtime/models/<model_id>.json`

Request bodies contain IDs only. Paths and URLs are rejected. The service revalidates the complete weather snapshot against `as_of` and resolves model metadata, schema and cutoffs before inference.

## Endpoints

`POST /forecast-runs` returns HTTP 202:

```json
{
  "as_of": "2026-02-06T00:00:00Z",
  "horizon": 48,
  "model_id": "linear-v1",
  "weather_snapshot_id": "gfs-feb6",
  "mode": "replay"
}
```

Read state with:

- `GET /forecast-runs/{id}`
- `GET /forecast-runs/{id}/forecast`
- `GET /forecast-runs/{id}/audit`
- `GET /forecast-runs/{id}/events`
- `GET /health`

Create an immutable child revision with `POST /forecast-runs/{id}/recalculate`:

```json
{"weather_snapshot_id":"gfs-feb7","as_of":"2026-02-07T00:00:00Z"}
```

`as_of` is the new information cutoff and first target hour. A newer snapshot cannot be attached to the parent's old cutoff. In live mode the scheduler only accepts a snapshot whose origin equals the current UTC hour. Replay events use the snapshot's explicit target origin. Repeated input content returns the existing revision.

If the model is missing or incompatible, the run becomes `BLOCKED`, the audit records the reason and `/forecast` returns an empty `points` list. The backend never fabricates power values. The included linear JSON adapter is an explicit model bundle format; its predictions are only produced when the registered feature schema and temporal cutoffs validate.

## Service contract for tools

`ForecastService` exposes `create_run`, `get_run`, `get_forecast`, `get_audit`, `get_events`, `recalculate`, `process_one` and `tick_scheduler`. Agent tools should call these methods without bypassing the registered input directories or temporal validation.

# Forecast backend API

The backend persists immutable forecast runs, jobs, events and points in SQLite. One in-process worker leases durable jobs and recovers expired leases after a restart. A scheduler scans the registered snapshot inbox and creates one deduplicated child revision for each eligible new-origin snapshot.

## Run locally

Use Python 3.11–3.13:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-backend.txt
FORECAST_DATA_DIR=data/runtime .venv/bin/uvicorn backend.api.app:app
```

LightGBM needs an OpenMP runtime: install `libgomp1` on Linux or `libomp` on macOS before starting the service.

Register inputs by placing JSON files under the server-controlled directories:

- `data/runtime/snapshots/<weather_snapshot_id>.json`
- `data/runtime/models/<model_id>.json` for the legacy linear adapter, or `data/runtime/models/<model_id>/` for native SCADA/GFS bundles.

Request bodies contain IDs only. Paths and URLs are rejected. The service revalidates the complete weather snapshot against `as_of` and resolves model metadata, schema and cutoffs before inference.

To run the committed native bundle against the included archived 48-hour GFS fixture:

```bash
mkdir -p data/runtime/snapshots data/runtime/models
cp data/fixtures/noaa-gfs-20260206T000000Z-h48.json data/runtime/snapshots/gfs-feb6.json
cp -R artifacts/models/brev-scada-pooled-lgbm-20260201 data/runtime/models/
FORECAST_DATA_DIR=data/runtime .venv/bin/uvicorn backend.api.app:app
```

Then submit `model_id` `brev-scada-pooled-lgbm-20260201`, `weather_snapshot_id` `gfs-feb6`, and origin `2026-02-06T00:00:00Z`. The adapter checks `model.txt` SHA-256, schema, cutoff (`2026-01-31T19:00:00Z`) and fixed mapping: GFS `wind_speed_100m` and `temperature_2m`, plus wind squared/cubed and the target start converted from UTC to `Asia/Almaty`. `T1` and `T2` map to numeric IDs 1 and 2. The forecast response carries the experimental-input warning; the audit response records bundle limitations. The local hour-start convention is an explicit training assumption; no operational accuracy has been measured.

## Archived-GFS models and selected baseline

For origins at `06:00 UTC` and a 48-hour horizon, use the selected `gfs-power-curve-mvp-20260131` bundle. Its January 2026 validation MAE was 0.2044 on 384 labeled rows, compared with 0.2472 for `gfs-pooled-lgbm-mvp-20260131` on exactly the same rows. The old SCADA-weather MAE 0.0308 uses observed weather and a different validation population; it is not an operational GFS forecast score. Both GFS bundles have training cutoff `2026-01-30T18:00:00Z`, before the assumed first 31 January origin `06:00 UTC`. The SCADA publication delay is an unverified zero-hour assumption.

```bash
cp data/fixtures/noaa-gfs-20260206T060000Z-h48.json data/runtime/snapshots/gfs-feb6-06.json
cp -R artifacts/models/gfs-power-curve-mvp-20260131 data/runtime/models/
cp -R artifacts/models/gfs-pooled-lgbm-mvp-20260131 data/runtime/models/
```

Submit `as_of=2026-02-06T06:00:00Z`, `horizon=48`, `weather_snapshot_id=gfs-feb6-06`, and `model_id=gfs-power-curve-mvp-20260131` to `POST /forecast-runs`. The worker validates schedule, source availability, bundle checksums, schema and cutoff; it returns 96 clipped power points or blocks the run. The LightGBM model uses the shared `backend/forecasting/gfs_features.py` contract in training and inference, and returns an explicit warning because it lost to the baseline. Neither bundle has measured February accuracy because the supplied CSVs end in January.

## Endpoints

`POST /forecast-runs` returns HTTP 202:

```json
{
  "as_of": "2026-02-06T00:00:00Z",
  "horizon": 48,
  "model_id": "brev-scada-pooled-lgbm-20260201",
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

If the model is missing, incompatible or tampered, the run becomes `BLOCKED`, the audit records the reason and `/forecast` returns an empty `points` list. The backend never fabricates power values. The legacy linear JSON adapter remains available; native directory bundles are loaded only when their registered files remain contained in the server-controlled model directory.

## Service contract for tools

`ForecastService` exposes `create_run`, `get_run`, `get_forecast`, `get_audit`, `get_events`, `recalculate`, `process_one` and `tick_scheduler`. Agent tools should call these methods without bypassing the registered input directories or temporal validation.

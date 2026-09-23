# Brev training run — 2026-09-23

Location: `/home/ubuntu/turbine-training` on `brev-ftly7vjxp`.

The user confirmed `Asia/Almaty` for the source clock. The CSVs do not specify
the first forecast origin, SCADA publication delay, or a physical wind limit.
This exploratory run used:

| Parameter | Assumption |
| --- | --- |
| First forecast origin | `2026-02-01 00:00:00` source time, just after the last CSV hour |
| SCADA delay | 0 hours |
| Maximum valid wind speed | 30 m/s, above the observed maximum of 22.97 m/s |

The uploaded CSV SHA256 values matched the local originals. On Brev, Python
3.12.14 was used. The VM lacked `libgomp.so.1`; installing Ubuntu `libgomp1`
resolved LightGBM import. All six unit tests then passed. Under `Asia/Almaty`,
each CSV contains six ambiguous local timestamps at the 2024 clock change.
They remain in source-clock time because the CSVs have no UTC offset or fold
indicator; this ambiguity does not touch the 2025-11-01 split boundary.
`nvidia-smi` was unavailable; this run used the script's CPU LightGBM path.

Training command:

```bash
.venv/bin/python train_brev.py --config config.brev-assumptions.json \
  --turbine-1 turbine_1.csv --turbine-2 turbine_2.csv \
  --out artifacts > train.log 2>&1
```

The full validation period contained 4,402 hourly rows: 2,204 from turbine 1
and 2,198 from turbine 2. All three candidates used the same row-key hash
within validation and model selection. Selection and validation were identical
under the assumed origin and zero delay.

| Model | Overall MAE | Overall RMSE | Overall R² |
| --- | ---: | ---: | ---: |
| Power curve | 0.044877 | 0.077251 | 0.956113 |
| Pooled LightGBM | **0.030807** | **0.066852** | **0.967133** |
| Separate LightGBM | 0.031147 | 0.070978 | 0.962951 |

The winner by mean turbine MAE was `pooled_lgbm`. Its turbine 1 metrics were
MAE 0.031089, RMSE 0.061559, R² 0.972268; turbine 2 metrics were MAE
0.030525, RMSE 0.071769, R² 0.961920. The final pooled model was refit on
all labels assumed available by the origin and saved as
`artifacts/best_model.joblib` (1.5 MB), with `artifacts/model.txt`,
`artifacts/metrics.json`, and `artifacts/data_analysis.json`.

A separate Python process loaded `best_model.joblib` on the Brev CPU and
produced bounded forecasts for both turbine IDs. The model comparison metrics
describe candidates trained before 2025-11-01, not the refit final artifact.
The result is conditional on actual historical wind and temperature, not an
archived weather forecast backtest. The assumed origin, zero SCADA delay, and
30 m/s bound require confirmation before operational use.

# Brev SCADA training handoff

The two original 10-minute CSVs are in `data/training/`. `train_brev.py` aggregates complete clock hours, compares a power-curve baseline, pooled LightGBM and per-turbine LightGBM on the same validation rows, then exports the winner. It was run on Brev CPU on 2026-09-23; the repository contains the resulting pooled native LightGBM `model.txt` under `artifacts/models/brev-scada-pooled-lgbm-20260201/`.

The CSV clock timezone is `Asia/Almaty`, confirmed by the data owner. The first forecast origin `2026-02-01 00:00` local, zero SCADA publication delay, and 30 m/s sensor maximum are **assumptions** because the CSVs do not document them. Six timestamps per turbine around the 2024 local clock change are ambiguous as UTC instants. Splits use source-clock timestamps; this ambiguity does not affect the 2025-11-01 boundary.

To reproduce training on Linux CPU:

```bash
sudo apt-get install -y libgomp1
python3 -m venv .venv
. .venv/bin/activate
pip install -r ml/requirements-brev.txt
python -m unittest discover -s ml -p 'test_train_brev.py' -v
python ml/train_brev.py --config ml/config.brev-assumptions.json \
  --turbine-1 data/training/turbine_1.csv \
  --turbine-2 data/training/turbine_2.csv --out reproduced
```

This command retrains. To verify the saved model without retraining, run `python ml/verify_model.py`. The bundle's `cpu_fixture.json` contains two predictions verified against `best_model.joblib` on Brev; `data_manifest.json` records source hashes and the measured-weather validation summary. The current backend does not yet load this bundle; its adapter is a separate refactor.

The metrics from observed weather cannot be used as an archived forecast backtest. GFS uses instantaneous 100 m wind and 2 m temperature, while SCADA training inputs were hourly measured averages. Evaluate the API against archived GFS and subsequently available labels before making accuracy claims.

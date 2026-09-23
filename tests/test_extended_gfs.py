"""Temporal gate checks for the expanded archived-GFS experiment."""

import pandas as pd

from ml.train_extended_gfs import subsets


def test_boundary_targets_and_publication_delay_are_purged():
    frame = pd.DataFrame({
        "origin_time": ["2025-11-29T06:00:00Z", "2025-11-30T06:00:00Z",
                        "2025-12-30T06:00:00Z", "2025-12-31T06:00:00Z",
                        "2026-01-29T06:00:00Z"],
        "target_end": ["2025-11-30T12:00:00Z", "2025-12-01T01:00:00Z",
                       "2025-12-31T12:00:00Z", "2026-01-01T01:00:00Z",
                       "2026-01-30T18:00:00Z"],
    })
    train, tune, holdout, refit = subsets(frame, 0)
    assert train.index.tolist() == [0]
    assert tune.index.tolist() == [2]
    assert holdout.index.tolist() == [4]
    assert refit.index.tolist() == [0, 1, 2, 3, 4]
    train_24, tune_24, holdout_24, refit_24 = subsets(frame, 24)
    assert train_24.empty
    assert tune_24.empty
    assert holdout_24.empty
    assert refit_24.index.tolist() == [0, 1, 2, 3]

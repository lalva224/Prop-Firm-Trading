"""Walk-forward tuning harness.

The purpose is NOT to find the best parameters. It is to measure whether
tuning helps at all - because the asset-selection experiment (docs/05) showed
that choosing by in-sample score bought in-sample Sharpe and paid for it with
out-of-sample Sharpe at roughly 1:1.

Protocol per fold:
  1. On the TRAIN slice, pick the parameter set with the best score.
  2. Record that set's score on the TEST slice it never saw.
  3. Record the FIXED DEFAULT's score on the same TEST slice.

If tuned test scores do not beat the fixed default's test scores across folds,
tuning is noise and the default should be kept. That is the finding this tool
exists to produce, and "do not tune" is a perfectly good answer.

Also returns `n_trials`, the cumulative count of parameter sets evaluated, so
the multiple-testing bar can be computed honestly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np
import pandas as pd


@dataclass
class Fold:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def make_folds(
    index: pd.DatetimeIndex,
    train_days: int = 1512,   # ~6 years
    test_days: int = 504,     # ~2 years
    step_days: int = 504,
) -> list[Fold]:
    """Rolling, strictly forward folds. No test slice precedes its train slice."""
    folds = []
    i = 0
    while i + train_days + test_days <= len(index):
        folds.append(
            Fold(index[i], index[i + train_days - 1],
                 index[i + train_days], index[i + train_days + test_days - 1])
        )
        i += step_days
    return folds


def walk_forward(
    score_fn: Callable[[dict, pd.Timestamp, pd.Timestamp], float],
    grid: Sequence[dict],
    index: pd.DatetimeIndex,
    default: dict,
    train_days: int = 1512,
    test_days: int = 504,
    step_days: int = 504,
) -> tuple[pd.DataFrame, dict]:
    """Run the protocol. `score_fn(params, start, end) -> score` (higher better)."""
    folds = make_folds(index, train_days, test_days, step_days)
    rows = []
    trials = 0

    for k, f in enumerate(folds):
        train_scores = {}
        for params in grid:
            train_scores[tuple(sorted(params.items()))] = score_fn(
                params, f.train_start, f.train_end
            )
            trials += 1
        best_key = max(train_scores, key=train_scores.get)
        best = dict(best_key)

        rows.append({
            "fold": k,
            "test_from": f.test_start.date(),
            "test_to": f.test_end.date(),
            "chosen": ", ".join(f"{a}={b}" for a, b in best_key),
            "train_score_best": train_scores[best_key],
            "test_score_tuned": score_fn(best, f.test_start, f.test_end),
            "test_score_default": score_fn(default, f.test_start, f.test_end),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df, {"n_trials": trials}

    diff = df.test_score_tuned - df.test_score_default
    summary = {
        "n_folds": len(df),
        "n_trials": trials,
        "mean_train_best": float(df.train_score_best.mean()),
        "mean_test_tuned": float(df.test_score_tuned.mean()),
        "mean_test_default": float(df.test_score_default.mean()),
        "tuning_edge": float(diff.mean()),
        "folds_tuning_won": int((diff > 0).sum()),
        "in_sample_inflation": float(df.train_score_best.mean() - df.test_score_tuned.mean()),
        "verdict": (
            "TUNE: tuned beat default in a majority of folds"
            if (diff > 0).mean() > 0.5 and diff.mean() > 0
            else "DO NOT TUNE: the default is as good or better out of sample"
        ),
    }
    return df, summary

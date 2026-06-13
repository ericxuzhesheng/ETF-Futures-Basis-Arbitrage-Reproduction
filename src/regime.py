"""Basis-regime identification for premium/discount environment switches.

The backtest signal answers "when to trade"; this module answers the separate
state question: is the market in futures premium, futures discount, or neutral?
It uses hysteresis plus a short confirmation window so roll-date noise and
single-day dividend adjustment jumps do not create fake regime switches.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import RegimeParams, REGIME


REGIME_LABELS = {
    1: "premium",
    0: "neutral",
    -1: "discount",
}


def identify_basis_regime(df: pd.DataFrame, params: RegimeParams = REGIME) -> pd.DataFrame:
    """Attach basis-regime columns to a frame with basis-rate columns.

    Required columns:
      - ``basis_rate`` when ``use_dividend_adjusted`` is true
      - otherwise ``basis_rate_raw``

    Returns a copy with ``regime``, ``regime_label``, ``regime_switch`` and
    ``regime_run_days``.
    """
    source_col = "basis_rate" if params.use_dividend_adjusted else "basis_rate_raw"
    if source_col not in df.columns:
        raise KeyError(f"{source_col} column is required for basis-regime detection")

    rate = df[source_col].to_numpy(dtype=float)
    regime = np.zeros(len(df), dtype=int)
    state = 0
    pending = 0
    pending_days = 0

    for i, r in enumerate(rate):
        if not np.isfinite(r):
            regime[i] = state
            continue

        desired = 0
        if r >= params.enter_rate:
            desired = 1
        elif r <= -params.enter_rate:
            desired = -1
        elif state == 1 and r > params.exit_rate:
            desired = 1
        elif state == -1 and r < -params.exit_rate:
            desired = -1

        if desired == state:
            pending = 0
            pending_days = 0
            regime[i] = state
            continue
        if desired == 0:
            state = 0
            pending = 0
            pending_days = 0
            regime[i] = state
            continue

        if desired != state:
            # A side switch must be confirmed. During the confirmation window
            # report neutral rather than carrying the stale opposite regime.
            if desired == pending:
                pending_days += 1
            else:
                pending = desired
                pending_days = 1
            if pending_days >= params.min_confirm_days:
                state = pending
                pending = 0
                pending_days = 0
                regime[i] = state
            else:
                regime[i] = 0
        else:
            regime[i] = state

    out = df.copy()
    out["regime"] = regime
    out["regime_label"] = pd.Series(regime, index=out.index).map(REGIME_LABELS)
    out["regime_switch"] = pd.Series(regime, index=out.index).diff().fillna(0).ne(0)
    groups = pd.Series(regime, index=out.index).ne(pd.Series(regime, index=out.index).shift()).cumsum()
    out["regime_run_days"] = groups.groupby(groups).cumcount() + 1
    return out


def regime_summary(df: pd.DataFrame, pair_name: str) -> pd.DataFrame:
    """Summarize contiguous regime runs for diagnostics and reporting."""
    if "regime" not in df.columns:
        raise KeyError("regime column is required; call identify_basis_regime first")
    if len(df) == 0:
        return pd.DataFrame(columns=[
            "pair", "regime", "regime_label", "start", "end", "days",
            "mean_basis_rate", "min_basis_rate", "max_basis_rate",
        ])

    work = df.copy()
    work["_group"] = work["regime"].ne(work["regime"].shift()).cumsum()
    idx_as_series = pd.Series(work.index, index=work.index)
    rows = []
    for _, g in work.groupby("_group"):
        rows.append({
            "pair": pair_name,
            "regime": int(g["regime"].iloc[0]),
            "regime_label": REGIME_LABELS[int(g["regime"].iloc[0])],
            "start": idx_as_series.loc[g.index].iloc[0],
            "end": idx_as_series.loc[g.index].iloc[-1],
            "days": int(len(g)),
            "mean_basis_rate": float(g["basis_rate"].mean()),
            "min_basis_rate": float(g["basis_rate"].min()),
            "max_basis_rate": float(g["basis_rate"].max()),
        })
    return pd.DataFrame(rows)

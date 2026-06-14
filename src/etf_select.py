"""Dynamic ETF selection for the spot leg (东证: 按流动性/跟踪误差/折溢价择优).

For each pair we may have several ETFs tracking the same index. Every
ETF_REBALANCE_DAYS we score the candidates and pick the best spot leg:

    score = z(liquidity) - z(tracking_error)        # 更流动、更低跟踪误差者优

The chosen ETF is held until the next rebalance. We return, on the common
calendar, the selected ETF's total return excess over the index (tracking
term that feeds the basis P&L) plus its 折溢价, and a label of which ETF is
active — so the backtest can compare fixed vs dynamic spot legs.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import ETF_REBALANCE_DAYS, ETF_TE_WINDOW, ETF_LIQ_WINDOW
from src import data_tushare as dt


def _zscore_cross(frame: pd.DataFrame) -> pd.DataFrame:
    """Row-wise cross-sectional z-score (across candidate ETFs)."""
    mu = frame.mean(axis=1)
    sd = frame.std(axis=1).replace(0.0, np.nan)
    return frame.sub(mu, axis=0).div(sd, axis=0).fillna(0.0)


def select(candidates: tuple[str, ...], index_df: pd.DataFrame,
           start: str, end: str | None) -> pd.DataFrame:
    """Return [trade_date, chosen, track_excess, premium] for the spot leg.

    index_df has [trade_date, spot]. track_excess = r_chosen - r_index.
    """
    idx = index_df[["trade_date", "spot"]].copy()
    idx["r_index"] = idx["spot"].pct_change(fill_method=None)

    # Build aligned per-candidate panels.
    closes, amounts, prems = {}, {}, {}
    for c in candidates:
        p = dt.etf_panel(c, start, end).merge(idx[["trade_date"]], on="trade_date", how="right")
        p = p.sort_values("trade_date")
        closes[c] = p.set_index("trade_date")["adj_close"]
        amounts[c] = p.set_index("trade_date")["amount"]
        prems[c] = p.set_index("trade_date")["premium"]
    close_df = pd.DataFrame(closes)
    amt_df = pd.DataFrame(amounts)
    prem_df = pd.DataFrame(prems)
    ret_df = close_df.pct_change(fill_method=None)
    r_index = idx.set_index("trade_date")["r_index"].reindex(close_df.index)

    # Single candidate -> degenerate (e.g. 上证50).
    if len(candidates) == 1:
        c = candidates[0]
        out = pd.DataFrame({
            "trade_date": close_df.index,
            "chosen": c,
            "track_excess": (ret_df[c] - r_index).fillna(0.0).values,
            "premium": prem_df[c].fillna(0.0).values,
        })
        return out.reset_index(drop=True)

    # Scores: liquidity (rolling mean amount) and tracking error (rolling std of
    # candidate-minus-index return). Higher liquidity good, lower TE good.
    liq = amt_df.rolling(ETF_LIQ_WINDOW, min_periods=5).mean()
    te = ret_df.sub(r_index, axis=0).rolling(ETF_TE_WINDOW, min_periods=20).std()
    score = _zscore_cross(liq) - _zscore_cross(te)

    # Pick best at each rebalance date, hold until next (positional, robust).
    dates = close_df.index
    cols = list(score.columns)
    score_vals = score.to_numpy()
    chosen_list = []
    current = candidates[0]
    for i in range(len(dates)):
        if i % ETF_REBALANCE_DAYS == 0:
            rowv = score_vals[i]
            if np.isfinite(rowv).any():
                current = cols[int(np.nanargmax(np.where(np.isfinite(rowv), rowv, -np.inf)))]
        chosen_list.append(current)
    chosen = pd.Series(chosen_list, index=dates)

    track = pd.Series(0.0, index=dates)
    prem = pd.Series(0.0, index=dates)
    for c in candidates:
        mask = (chosen == c).values
        track[mask] = (ret_df[c] - r_index).fillna(0.0).values[mask]
        prem[mask] = prem_df[c].fillna(0.0).values[mask]

    return pd.DataFrame({
        "trade_date": dates,
        "chosen": chosen.values,
        "track_excess": track.values,
        "premium": prem.values,
    }).reset_index(drop=True)

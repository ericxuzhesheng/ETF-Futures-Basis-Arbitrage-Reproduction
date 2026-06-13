"""Performance metrics for the basis-arbitrage backtest.

Operates on a daily strategy-return Series (already net of costs) indexed by
trade_date. All annualisation uses config.TRADING_DAYS.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import TRADING_DAYS


def equity_curve(daily_ret: pd.Series) -> pd.Series:
    return (1.0 + daily_ret.fillna(0.0)).cumprod()


def ann_return(daily_ret: pd.Series) -> float:
    eq = equity_curve(daily_ret)
    n = len(daily_ret)
    if n == 0 or eq.iloc[-1] <= 0:
        return float("nan")
    return eq.iloc[-1] ** (TRADING_DAYS / n) - 1.0


def ann_vol(daily_ret: pd.Series) -> float:
    return daily_ret.std(ddof=0) * np.sqrt(TRADING_DAYS)


def sharpe(daily_ret: pd.Series, rf: float = 0.0) -> float:
    v = ann_vol(daily_ret)
    if v == 0 or np.isnan(v):
        return float("nan")
    return (ann_return(daily_ret) - rf) / v


def max_drawdown(daily_ret: pd.Series) -> float:
    eq = equity_curve(daily_ret)
    peak = eq.cummax()
    return float((eq / peak - 1.0).min())


def trade_stats(position: pd.Series) -> dict:
    """Count trades and mean holding period (consecutive non-zero runs)."""
    pos = position.fillna(0).to_numpy()
    trades, run, holds = 0, 0, []
    prev = 0
    for p in pos:
        if p != 0 and prev == 0:
            trades += 1
            run = 1
        elif p != 0 and prev != 0:
            run += 1
        elif p == 0 and prev != 0:
            holds.append(run)
            run = 0
        prev = p
    if run > 0:
        holds.append(run)
    return {
        "n_trades": trades,
        "avg_hold_days": float(np.mean(holds)) if holds else 0.0,
        "exposure": float((pos != 0).mean()),
    }


def win_rate(daily_ret: pd.Series, position: pd.Series) -> float:
    """Fraction of active days with positive return (proxy for trade win rate)."""
    active = daily_ret[position.fillna(0) != 0]
    if len(active) == 0:
        return float("nan")
    return float((active > 0).mean())


def summarize(daily_ret: pd.Series, position: pd.Series, rf: float = 0.0) -> dict:
    out = {
        "ann_return": ann_return(daily_ret),
        "ann_vol": ann_vol(daily_ret),
        "sharpe": sharpe(daily_ret, rf),
        "max_dd": max_drawdown(daily_ret),
        "win_rate": win_rate(daily_ret, position),
    }
    out.update(trade_stats(position))
    return out

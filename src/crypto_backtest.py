"""Track B realized P&L for the Binance spot–perpetual cash-and-carry.

Unlike Track 0 (which *locks* the carry at entry because A-share dividends and
delivery convergence are only partially observable), the crypto legs are fully
observable — spot, perp and realized funding are all in the data — so the daily
P&L is computed directly, with no locked-carry assumption:

    +1  long spot / short perp:  + (r_spot - r_perp) + funding_daily
    -1  short spot / long perp:  - (r_spot - r_perp) - funding_daily
     0  flat:                    rf/day on idle USDT

Position changes are charged ``costs.exec_one_way`` (both legs). Annualisation
uses 365 (crypto trades 24/7), so metrics that depend on the trading-day count
are reimplemented here; the path/trade metrics are reused from :mod:`src.metrics`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import CRYPTO_COSTS, CRYPTO_DAYS, CryptoCosts
from src import metrics


def simulate(df: pd.DataFrame, pos: pd.Series,
             costs: CryptoCosts = CRYPTO_COSTS) -> pd.Series:
    """Net daily return of the notional-neutral cash-and-carry, net of costs."""
    pos = pos.reindex(df.index).fillna(0).astype(int)
    pos_lag = pos.shift(1).fillna(0)
    r_spot = df["spot"].pct_change(fill_method=None).fillna(0.0)
    r_perp = df["perp"].pct_change(fill_method=None).fillna(0.0)

    carry = pos_lag * (r_spot - r_perp + df["funding_daily"])
    idle = (pos_lag == 0).astype(float) * (costs.rf / CRYPTO_DAYS)
    dpos = pos.diff().abs().fillna(pos.abs())
    cost = dpos * costs.exec_one_way
    return (carry + idle - cost).fillna(0.0).rename("net_ret")


def ann_return(daily_ret: pd.Series) -> float:
    eq = metrics.equity_curve(daily_ret)
    n = len(daily_ret)
    if n == 0 or eq.iloc[-1] <= 0:
        return float("nan")
    return eq.iloc[-1] ** (CRYPTO_DAYS / n) - 1.0


def ann_vol(daily_ret: pd.Series) -> float:
    return daily_ret.std(ddof=0) * np.sqrt(CRYPTO_DAYS)


def summarize(daily_ret: pd.Series, position: pd.Series, rf: float = 0.0) -> dict:
    a, v = ann_return(daily_ret), ann_vol(daily_ret)
    out = {
        "ann_return": a,
        "ann_vol": v,
        "sharpe": float("nan") if (v == 0 or np.isnan(v)) else (a - rf) / v,
        "max_dd": metrics.max_drawdown(daily_ret),
        "win_rate": metrics.win_rate(daily_ret, position),
    }
    out.update(metrics.trade_stats(position))
    return out

"""Signal generators for the reproduced strategy variants.

Position convention (notional-neutral, one unit of spot notional per leg):
    +1  正向套利  long ETF  / short futures   (captures 期货升水 basis convergence)
    -1  反向套利  short ETF / long futures    (captures 期货贴水, needs 融券)
     0  flat

The long-ETF/short-futures spread only earns a *positive* return when the
futures trades at a premium (升水, basis>0) and that premium converges. So when
融券 is disallowed (allow_short_etf=False) every generator is gated to take the
+1 side only while the annualised basis rate is genuinely positive — this is the
honest A-share reality the reports stress (反向套利受融券约束).

All generators are causal (rolling, no lookahead) and return an int position
Series aligned to df.index.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import SignalParams


def _rolling_percentile_rank(s: pd.Series, window: int) -> pd.Series:
    def rank(x: np.ndarray) -> float:
        return (x[-1] >= x).mean()
    return s.rolling(window, min_periods=max(20, window // 5)).apply(rank, raw=True)


def convergence_position(df: pd.DataFrame, p: SignalParams) -> pd.Series:
    """基差收敛 (银河/东证): hold the carry while the annualised basis is rich.

    Enter +1 when 升水 exceeds the entry rate; hold (collecting convergence)
    until the basis decays below the exit rate. Mirror to -1 for 贴水 only when
    融券 is allowed.
    """
    rate = df["basis_rate"].to_numpy()
    pos = np.zeros(len(df), dtype=int)
    state = 0
    for i, r in enumerate(rate):
        if np.isnan(r):
            pos[i] = state
            continue
        if state == 0:
            if r >= p.conv_enter_rate:
                state = 1
            elif r <= -p.conv_enter_rate and p.allow_short_etf:
                state = -1
        elif state == 1 and r <= p.conv_exit_rate:
            state = 0
        elif state == -1 and r >= -p.conv_exit_rate:
            state = 0
        pos[i] = state
    return pd.Series(pos, index=df.index, name="pos_conv")


def galaxy_percentile_position(df: pd.DataFrame, p: SignalParams) -> pd.Series:
    """银河式 滚动历史分位数阈值, gated to positive-carry when 融券 disabled.

    >enter_high_pct -> 正向 (+1) but only while basis>0; <enter_low_pct -> 反向
    (-1) only if allow_short_etf. Exit toward the median.
    """
    rank = _rolling_percentile_rank(df["basis_rate"], p.percentile_window).to_numpy()
    rate = df["basis_rate"].to_numpy()
    pos = np.zeros(len(df), dtype=int)
    state = 0
    for i in range(len(df)):
        r, br = rank[i], rate[i]
        if np.isnan(r):
            pos[i] = state
            continue
        if state == 0:
            if r >= p.enter_high_pct and br > 0:
                state = 1
            elif r <= p.enter_low_pct and br < 0 and p.allow_short_etf:
                state = -1
        elif state == 1 and (r <= p.exit_pct or br <= 0):
            state = 0
        elif state == -1 and (r >= p.exit_pct or br >= 0):
            state = 0
        pos[i] = state
    return pd.Series(pos, index=df.index, name="pos_galaxy")


def orient_zscore_position(df: pd.DataFrame, p: SignalParams) -> pd.Series:
    """东证式 均值回归 z-score, gated to positive-carry when 融券 disabled."""
    s = df["basis_rate"]
    mean = s.rolling(p.zscore_window, min_periods=p.zscore_window // 2).mean()
    std = s.rolling(p.zscore_window, min_periods=p.zscore_window // 2).std()
    z = ((s - mean) / std).to_numpy()
    rate = s.to_numpy()
    pos = np.zeros(len(df), dtype=int)
    state = 0
    for i in range(len(df)):
        zi, br = z[i], rate[i]
        if np.isnan(zi):
            pos[i] = state
            continue
        if state == 0:
            if zi >= p.z_enter and br > 0:
                state = 1
            elif zi <= -p.z_enter and br < 0 and p.allow_short_etf:
                state = -1
        elif abs(zi) <= p.z_exit:
            state = 0
        elif state == 1 and br <= 0:
            state = 0
        elif state == -1 and br >= 0:
            state = 0
        pos[i] = state
    return pd.Series(pos, index=df.index, name="pos_orient")

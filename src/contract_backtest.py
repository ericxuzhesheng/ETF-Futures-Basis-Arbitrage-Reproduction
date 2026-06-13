"""逐合约持有至交割 (per-contract hold-to-delivery) accounting.

Rigorous alternative to the continuous-main locked-carry approximation: a trade
is opened in ONE specific futures contract and marked-to-market on that same
contract until it is closed near delivery. There are no roll jumps and the basis
convergence is realised on the actual contract (F -> S at delivery).

Single sleeve per pair: at most one open trade. When flat we look at the front
tradeable contract (smallest dte above the entry floor); if its dividend-adjusted
annualised basis exceeds the threshold we open long-ETF/short-future (升水) — or
the short-ETF/long-future mirror when 融券 is allowed (贴水). The trade is held
until the basis converges or delivery approaches.

Daily P&L = side * (r_ETF_total - r_future_same_contract), which contains the
basis convergence, ETF tracking and realised (seasonal) dividends in one place —
no double counting, no carry heuristic.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import (COSTS, SIGNALS, CONTRACT_ENTRY_MIN_DTE, CONTRACT_ROLL_BUFFER,
                    TRADING_DAYS, DIV_YIELD_WINDOW)
from src import data_tushare as dt

_OPEN_COST = COSTS.exec_one_way


def _base_frame(pair, start: str, end: str | None) -> pd.DataFrame:
    """trade_date index with spot, etf_adj_close, div_yield."""
    idx = dt.index_close(pair.index_code, start, end)
    etf = dt.etf_close(pair.etf_code, start, end)
    div = dt.dividend_yield(pair.index_code, pair.tr_code, start, end, DIV_YIELD_WINDOW)
    df = idx.merge(etf, on="trade_date", how="inner").merge(div, on="trade_date", how="left")
    df["div_yield"] = df["div_yield"].ffill().bfill().fillna(0.0)
    return df.sort_values("trade_date").set_index("trade_date")


def run_pair(pair, start: str, end: str | None, signals=SIGNALS) -> dict:
    """Per-contract hold-to-delivery backtest for one pair.

    Returns {pair, net (daily return Series), n_trades, avg_hold, trades}.
    """
    pstart = max(start, pair.start_date)
    base = _base_frame(pair, pstart, end)
    panel = dt.futures_contracts_panel(pair.fut_code, pstart, end)

    # Per-contract lookups: fut_close & dte by date, and intra-contract returns.
    fut_by_c, dte_by_c, ret_by_c = {}, {}, {}
    for c, g in panel.groupby("contract"):
        g = g.sort_values("trade_date").set_index("trade_date")
        fut_by_c[c] = g["fut_close"]
        dte_by_c[c] = g["dte"]
        ret_by_c[c] = g["fut_close"].pct_change()
    # Front contract per date = smallest dte at/above the entry floor.
    front = (panel[panel["dte"] >= CONTRACT_ENTRY_MIN_DTE]
             .sort_values(["trade_date", "dte"])
             .groupby("trade_date").first())  # columns: contract, fut_close, dte

    dates = base.index
    r_etf = base["etf_adj_close"].pct_change()
    spot = base["spot"]
    divy = base["div_yield"]

    net = pd.Series(0.0, index=dates)
    state = 0           # 0 flat, +1 long-ETF/short-fut, -1 short-ETF/long-fut
    held = None         # contract code
    trades, hold_len = [], 0
    entry_info = None

    for d in dates:
        if state == 0:
            # try to open in the front contract
            if d in front.index:
                c = front.at[d, "contract"]
                F = front.at[d, "fut_close"]
                dtev = front.at[d, "dte"]
                S = spot.get(d, np.nan)
                if np.isfinite(F) and np.isfinite(S) and S > 0:
                    rate = (F - S) / S * 365.0 / max(dtev, 7) + divy.get(d, 0.0)
                    side = 0
                    if rate >= signals.conv_enter_rate:
                        side = 1
                    elif rate <= -signals.conv_enter_rate and signals.allow_short_etf:
                        side = -1
                    if side != 0:
                        state, held, hold_len = side, c, 0
                        entry_info = {"contract": c, "entry": d, "side": side,
                                      "entry_rate": rate}
                        net[d] = -_OPEN_COST  # open cost
                        continue
            net[d] = COSTS.rf / TRADING_DAYS  # idle
            continue

        # state != 0: holding `held`
        r_fut = ret_by_c[held].get(d, np.nan)
        dtev = dte_by_c[held].get(d, np.nan)
        if not np.isfinite(r_fut):
            # contract not trading today (rare) -> hold flat-ish, no pnl
            net[d] = 0.0
            continue
        day_pnl = state * (r_etf.get(d, 0.0) - r_fut)
        net[d] = day_pnl
        hold_len += 1

        # 持有至交割, 但基差提前收敛则提前了结以释放资金 (realistic desk behaviour):
        # exit when delivery approaches, or the basis has converged through the band.
        S = spot.get(d, np.nan)
        F = fut_by_c[held].get(d, np.nan)
        rate = ((F - S) / S * 365.0 / max(dtev, 7) + divy.get(d, 0.0)) \
            if np.isfinite(S) and S > 0 and np.isfinite(F) else 0.0
        converged = (state == 1 and rate <= signals.conv_exit_rate) or \
                    (state == -1 and rate >= -signals.conv_exit_rate)
        if dtev <= CONTRACT_ROLL_BUFFER or converged:
            net[d] += -_OPEN_COST  # close cost
            entry_info.update({"exit": d, "hold_days": hold_len})
            trades.append(entry_info)
            state, held, hold_len, entry_info = 0, None, 0, None

    holds = [t["hold_days"] for t in trades] or [0]
    return {"pair": pair.name, "net": net, "n_trades": len(trades),
            "avg_hold": float(np.mean(holds)), "trades": trades}

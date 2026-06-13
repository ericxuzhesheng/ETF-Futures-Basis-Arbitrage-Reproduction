"""Track A execution-matching proxy for ETF/futures basis trades.

The production Track A target is hftbacktest over L2 snapshots. This module
keeps the same execution concepts (child orders, passive queue, crossing after
timeout, fill quality) but builds a deterministic synthetic book from the daily
Track 0 frame so the execution layer can be tested before real snapshots arrive.
"""

from __future__ import annotations

from dataclasses import asdict

import numpy as np
import pandas as pd

from config import ExecutionParams, EXECUTION, Pair


def _round_to_tick(price: float, tick: float) -> float:
    return round(price / tick) * tick


def _synthetic_book(row: pd.Series, params: ExecutionParams) -> dict:
    fut_mid = float(row["fut_close"])
    etf_mid = float(row["etf_close"])
    fut_half = max(params.tick_size * params.spread_ticks / 2.0, fut_mid * 0.00005)
    etf_half = max(etf_mid * 0.00005, etf_mid * params.spread_ticks * 0.00001)
    depth_notional = max(params.target_notional * params.passive_depth_bps / 10_000.0,
                         params.target_notional / params.child_slices)
    return {
        "fut_bid": _round_to_tick(fut_mid - fut_half, params.tick_size),
        "fut_ask": _round_to_tick(fut_mid + fut_half, params.tick_size),
        "fut_mid": fut_mid,
        "etf_bid": etf_mid - etf_half,
        "etf_ask": etf_mid + etf_half,
        "etf_mid": etf_mid,
        "depth_notional": depth_notional,
    }


def _fill_one_leg(side: int, mid: float, bid: float, ask: float, child_notional: float,
                  queue_ahead: float, force_cross: bool,
                  params: ExecutionParams) -> tuple[float, float, bool]:
    """Return fill price, next queue ahead and whether the order crossed."""
    passive_price = bid if side < 0 else ask
    active_price = ask if side > 0 else bid
    consumed = child_notional * params.queue_decay
    next_queue = max(queue_ahead - consumed, 0.0)
    if next_queue <= 0 and not force_cross:
        return passive_price, 0.0, False
    cross_penalty = mid * params.active_cross_bps / 10_000.0
    crossed_price = active_price + side * cross_penalty
    return crossed_price, next_queue, True


def simulate_pair_execution(df: pd.DataFrame, pos: pd.Series, pair: Pair,
                            params: ExecutionParams = EXECUTION) -> tuple[pd.DataFrame, dict]:
    """Simulate child-order execution for target-position changes.

    ``pos`` follows the project convention:
      +1 = buy ETF / sell futures
      -1 = sell ETF / buy futures
    """
    if len(df) != len(pos):
        raise ValueError("df and pos must be aligned and have the same length")
    if params.child_slices <= 0:
        raise ValueError("child_slices must be positive")

    aligned_pos = pos.reindex(df.index).fillna(0).astype(int)
    orders = []
    queue_fut = 0.0
    queue_etf = 0.0

    for trade_date, row in df.iterrows():
        prev = int(aligned_pos.shift(1).fillna(0).get(trade_date, 0))
        target = int(aligned_pos.loc[trade_date])
        delta = target - prev
        if delta == 0:
            continue

        book = _synthetic_book(row, params)
        child_notional = params.target_notional * abs(delta) / params.child_slices
        fut_side = -np.sign(delta)  # +strategy buys ETF and sells futures
        etf_side = np.sign(delta)
        queue_fut = max(queue_fut, book["depth_notional"])
        queue_etf = max(queue_etf, book["depth_notional"])

        for child_id in range(1, params.child_slices + 1):
            fut_px, queue_fut, fut_cross = _fill_one_leg(
                int(fut_side), book["fut_mid"], book["fut_bid"], book["fut_ask"],
                child_notional, queue_fut, child_id > params.max_passive_wait_slices,
                params,
            )
            etf_px, queue_etf, etf_cross = _fill_one_leg(
                int(etf_side), book["etf_mid"], book["etf_bid"], book["etf_ask"],
                child_notional, queue_etf, child_id > params.max_passive_wait_slices,
                params,
            )
            fut_slip = (fut_px - book["fut_mid"]) / book["fut_mid"] * fut_side
            etf_slip = (etf_px - book["etf_mid"]) / book["etf_mid"] * etf_side
            orders.append({
                "trade_date": trade_date,
                "pair": pair.name,
                "delta_pos": int(delta),
                "child_id": child_id,
                "fut_side": int(fut_side),
                "etf_side": int(etf_side),
                "child_notional": child_notional,
                "fut_fill": fut_px,
                "etf_fill": etf_px,
                "fut_slippage_bps": fut_slip * 10_000.0,
                "etf_slippage_bps": etf_slip * 10_000.0,
                "crossed": bool(fut_cross or etf_cross),
            })

    fills = pd.DataFrame(orders)
    if fills.empty:
        summary = {
            "pair": pair.name,
            "orders": 0,
            "child_fills": 0,
            "cross_rate": 0.0,
            "mean_slippage_bps": 0.0,
            **{f"param_{k}": v for k, v in asdict(params).items()},
        }
        return fills, summary

    fills["total_slippage_bps"] = fills["fut_slippage_bps"] + fills["etf_slippage_bps"]
    summary = {
        "pair": pair.name,
        "orders": int(fills["trade_date"].nunique()),
        "child_fills": int(len(fills)),
        "cross_rate": float(fills["crossed"].mean()),
        "mean_slippage_bps": float(fills["total_slippage_bps"].mean()),
        "p95_slippage_bps": float(fills["total_slippage_bps"].quantile(0.95)),
        **{f"param_{k}": v for k, v in asdict(params).items()},
    }
    return fills, summary

"""Track A execution-matching layer for ETF/futures basis trades.

The production Track A target is hftbacktest over L2 snapshots. This module keeps
the execution concepts (child orders, passive queue, crossing after timeout, fill
quality) behind a pluggable :class:`BookProvider`:

  * :class:`SyntheticBookProvider` — deterministic book from the daily Track 0
    frame, so the execution layer is testable before any snapshot arrives.
  * :class:`SnapshotBookProvider` — real / self-recorded L2 snapshots
    (see :mod:`src.l2_snapshot`), falling back to the synthetic book for sessions
    that have not been recorded yet.

The runner output schema is identical for both providers; snapshot coverage is
reported as an extra diagnostic so a mixed real/synthetic run is auditable.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Protocol

import numpy as np
import pandas as pd

from config import ExecutionParams, EXECUTION, Pair
from src import l2_snapshot


def _round_to_tick(price: float, tick: float) -> float:
    return round(price / tick) * tick


# --------------------------------------------------------------------------- #
# Book providers
# --------------------------------------------------------------------------- #
class BookProvider(Protocol):
    """Supplies a per-leg order book for one instrument on one session.

    Returns a dict with ``bid``, ``ask``, ``mid``, ``bid_depth_notional``,
    ``ask_depth_notional`` and ``source`` ("synthetic" / "snapshot" /
    "snapshot_fallback").
    """

    def leg_book(self, trade_date, code: str, mid_hint: float,
                 size_multiplier: float, is_futures: bool,
                 params: ExecutionParams) -> dict:
        ...


class SyntheticBookProvider:
    """Deterministic book built from the daily mid (the original Track A proxy)."""

    def leg_book(self, trade_date, code: str, mid_hint: float,
                 size_multiplier: float, is_futures: bool,
                 params: ExecutionParams) -> dict:
        return _synthetic_leg_book(mid_hint, is_futures, params, source="synthetic")


class SnapshotBookProvider:
    """Real / self-recorded L2 book, falling back to synthetic when unrecorded."""

    def __init__(self, root=l2_snapshot.SNAPSHOT_ROOT,
                 fallback: BookProvider | None = None) -> None:
        self.root = root
        self.fallback = fallback or SyntheticBookProvider()

    def leg_book(self, trade_date, code: str, mid_hint: float,
                 size_multiplier: float, is_futures: bool,
                 params: ExecutionParams) -> dict:
        book = l2_snapshot.book_at(
            code, trade_date, params.snapshot_sample_time, size_multiplier, self.root,
        )
        if book is None:
            fb = self.fallback.leg_book(
                trade_date, code, mid_hint, size_multiplier, is_futures, params)
            fb["source"] = "snapshot_fallback"
            return fb
        book["source"] = "snapshot"
        return book


def _synthetic_leg_book(mid: float, is_futures: bool, params: ExecutionParams,
                        source: str) -> dict:
    """One leg's synthetic book — tick-based half-spread for futures, bps for ETF."""
    if is_futures:
        bid = _round_to_tick(mid - max(params.tick_size * params.spread_ticks / 2.0,
                                       mid * 0.00005), params.tick_size)
        ask = _round_to_tick(mid + max(params.tick_size * params.spread_ticks / 2.0,
                                       mid * 0.00005), params.tick_size)
    else:
        half = max(mid * 0.00005, mid * params.spread_ticks * 0.00001)
        bid, ask = mid - half, mid + half
    depth = max(params.target_notional * params.passive_depth_bps / 10_000.0,
                params.target_notional / params.child_slices)
    return {
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "bid_depth_notional": depth,
        "ask_depth_notional": depth,
        "source": source,
    }


# --------------------------------------------------------------------------- #
# Fill model
# --------------------------------------------------------------------------- #
def _fill_one_leg(side: int, book: dict, child_notional: float,
                  queue_ahead: float, force_cross: bool,
                  params: ExecutionParams) -> tuple[float, float, bool]:
    """Return fill price, next queue ahead and whether the order crossed."""
    mid, bid, ask = book["mid"], book["bid"], book["ask"]
    passive_price = bid if side < 0 else ask
    active_price = ask if side > 0 else bid
    consumed = child_notional * params.queue_decay
    next_queue = max(queue_ahead - consumed, 0.0)
    if next_queue <= 0 and not force_cross:
        return passive_price, 0.0, False
    cross_penalty = mid * params.active_cross_bps / 10_000.0
    crossed_price = active_price + side * cross_penalty
    return crossed_price, next_queue, True


def _passive_depth(book: dict, side: int) -> float:
    """Depth on the side we rest on: bid when selling, ask when buying."""
    return book["bid_depth_notional"] if side < 0 else book["ask_depth_notional"]


def simulate_pair_execution(df: pd.DataFrame, pos: pd.Series, pair: Pair,
                            params: ExecutionParams = EXECUTION,
                            book_provider: BookProvider | None = None,
                            ) -> tuple[pd.DataFrame, dict]:
    """Simulate child-order execution for target-position changes.

    ``pos`` follows the project convention:
      +1 = buy ETF / sell futures
      -1 = sell ETF / buy futures

    ``book_provider`` defaults to the synthetic book (original behaviour). Pass a
    :class:`SnapshotBookProvider` to drive fills from real / self-recorded L2.
    """
    if len(df) != len(pos):
        raise ValueError("df and pos must be aligned and have the same length")
    if params.child_slices <= 0:
        raise ValueError("child_slices must be positive")
    if book_provider is None:
        book_provider = SyntheticBookProvider()

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

        fut_code = str(row.get("mapping_ts_code", pair.fut_code))
        fut_side = int(-np.sign(delta))  # +strategy buys ETF and sells futures
        etf_side = int(np.sign(delta))
        fut_book = book_provider.leg_book(
            trade_date, fut_code, float(row["fut_close"]),
            float(pair.multiplier), True, params)
        etf_book = book_provider.leg_book(
            trade_date, pair.etf_code, float(row["etf_close"]),
            1.0, False, params)

        child_notional = params.target_notional * abs(delta) / params.child_slices
        queue_fut = max(queue_fut, _passive_depth(fut_book, fut_side))
        queue_etf = max(queue_etf, _passive_depth(etf_book, etf_side))

        for child_id in range(1, params.child_slices + 1):
            force = child_id > params.max_passive_wait_slices
            fut_px, queue_fut, fut_cross = _fill_one_leg(
                fut_side, fut_book, child_notional, queue_fut, force, params)
            etf_px, queue_etf, etf_cross = _fill_one_leg(
                etf_side, etf_book, child_notional, queue_etf, force, params)
            fut_slip = (fut_px - fut_book["mid"]) / fut_book["mid"] * fut_side
            etf_slip = (etf_px - etf_book["mid"]) / etf_book["mid"] * etf_side
            orders.append({
                "trade_date": trade_date,
                "pair": pair.name,
                "delta_pos": int(delta),
                "child_id": child_id,
                "fut_side": fut_side,
                "etf_side": etf_side,
                "child_notional": child_notional,
                "fut_fill": fut_px,
                "etf_fill": etf_px,
                "fut_slippage_bps": fut_slip * 10_000.0,
                "etf_slippage_bps": etf_slip * 10_000.0,
                "crossed": bool(fut_cross or etf_cross),
                "fut_source": fut_book["source"],
                "etf_source": etf_book["source"],
            })

    fills = pd.DataFrame(orders)
    if fills.empty:
        summary = {
            "pair": pair.name,
            "orders": 0,
            "child_fills": 0,
            "cross_rate": 0.0,
            "mean_slippage_bps": 0.0,
            "p95_slippage_bps": 0.0,
            "snapshot_fill_rate": 0.0,
            **{f"param_{k}": v for k, v in asdict(params).items()},
        }
        return fills, summary

    fills["total_slippage_bps"] = fills["fut_slippage_bps"] + fills["etf_slippage_bps"]
    snapshot_legs = (fills["fut_source"].eq("snapshot").astype(float)
                     + fills["etf_source"].eq("snapshot").astype(float))
    summary = {
        "pair": pair.name,
        "orders": int(fills["trade_date"].nunique()),
        "child_fills": int(len(fills)),
        "cross_rate": float(fills["crossed"].mean()),
        "mean_slippage_bps": float(fills["total_slippage_bps"].mean()),
        "p95_slippage_bps": float(fills["total_slippage_bps"].quantile(0.95)),
        "snapshot_fill_rate": float(snapshot_legs.mean() / 2.0),
        **{f"param_{k}": v for k, v in asdict(params).items()},
    }
    return fills, summary

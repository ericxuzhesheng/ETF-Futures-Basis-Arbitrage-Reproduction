"""Real / self-recorded L2 snapshot ingestion for Track A execution.

Track A's execution simulator was first built against a deterministic *synthetic*
book derived from daily closes. This module adds a pluggable book layer so real
self-recorded L2 snapshots can drive the same queue/fill model **without changing
the runner's output schema** — the synthetic book simply becomes the fallback for
sessions that have no recorded snapshot yet.

Self-recorded snapshot layout — one parquet per instrument per session:

    data/snapshots/<instrument_code>/<YYYYMMDD>.parquet

with the standard depth-5 columns (level 1 = best):

    ts                       intraday timestamp (datetime64[ns])
    bid_px_1 .. bid_px_5     bid prices
    bid_sz_1 .. bid_sz_5     bid sizes  (ETF: shares;  futures: lots)
    ask_px_1 .. ask_px_5     ask prices
    ask_sz_1 .. ask_sz_5     ask sizes

Any broker-terminal export or self-recording script only has to land this schema;
nothing else in the pipeline needs to change. Sizes are converted to CNY notional
with a per-instrument ``size_multiplier`` (1 for ETFs, contract multiplier for
index futures) so depth is comparable to ``target_notional``.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_ROOT = ROOT / "data" / "snapshots"

LEVELS = 5
BID_PX = [f"bid_px_{i}" for i in range(1, LEVELS + 1)]
BID_SZ = [f"bid_sz_{i}" for i in range(1, LEVELS + 1)]
ASK_PX = [f"ask_px_{i}" for i in range(1, LEVELS + 1)]
ASK_SZ = [f"ask_sz_{i}" for i in range(1, LEVELS + 1)]
REQUIRED_COLUMNS: tuple[str, ...] = ("ts", *BID_PX, *BID_SZ, *ASK_PX, *ASK_SZ)

# Cache successful parquet reads keyed by resolved path so repeated lookups across
# child orders within one backtest stay offline and fast.
_READ_CACHE: dict[str, pd.DataFrame] = {}


def clear_cache() -> None:
    """Drop the in-memory snapshot read cache (used by tests)."""
    _READ_CACHE.clear()


def snapshot_path(code: str, trade_date, root: Path = SNAPSHOT_ROOT) -> Path:
    """``data/snapshots/<code>/<YYYYMMDD>.parquet`` for one instrument+session."""
    day = pd.Timestamp(trade_date).strftime("%Y%m%d")
    return Path(root) / code / f"{day}.parquet"


def validate_snapshot_frame(df: pd.DataFrame) -> None:
    """Raise ``ValueError`` if a recorded snapshot frame is off-schema."""
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"snapshot frame missing columns: {missing}")
    if df.empty:
        raise ValueError("snapshot frame is empty")


def read_session_snapshots(code: str, trade_date,
                           root: Path = SNAPSHOT_ROOT) -> pd.DataFrame | None:
    """Read one instrument's intraday snapshots for a session, or ``None``.

    Returns ``None`` when no file exists so callers can fall back to the
    synthetic book. Validated frames are cached by path.
    """
    path = snapshot_path(code, trade_date, root)
    key = str(path)
    if key in _READ_CACHE:
        return _READ_CACHE[key]
    if not path.exists():
        return None
    frame = pd.read_parquet(path)
    validate_snapshot_frame(frame)
    frame = frame.copy()
    frame["ts"] = pd.to_datetime(frame["ts"])
    frame = frame.sort_values("ts").reset_index(drop=True)
    _READ_CACHE[key] = frame
    return frame


def _sample_row(frame: pd.DataFrame, sample_time: str) -> pd.Series:
    """Pick the last snapshot at/before ``sample_time`` (else the first row)."""
    cutoff = pd.to_timedelta(sample_time)
    tod = frame["ts"] - frame["ts"].dt.normalize()
    eligible = frame[tod <= cutoff]
    return eligible.iloc[-1] if not eligible.empty else frame.iloc[0]


def _side_depth_notional(row: pd.Series, px_cols, sz_cols,
                         size_multiplier: float) -> float:
    """Sum px*size*multiplier over valid levels on one side of the book."""
    total = 0.0
    for px_col, sz_col in zip(px_cols, sz_cols):
        px = float(row[px_col])
        sz = float(row[sz_col])
        if px > 0 and sz > 0:
            total += px * sz * size_multiplier
    return total


def book_from_row(row: pd.Series, size_multiplier: float = 1.0) -> dict:
    """Reduce a single L2 snapshot row to the execution-layer book dict."""
    bid = float(row["bid_px_1"])
    ask = float(row["ask_px_1"])
    return {
        "bid": bid,
        "ask": ask,
        "mid": (bid + ask) / 2.0,
        "bid_depth_notional": _side_depth_notional(row, BID_PX, BID_SZ, size_multiplier),
        "ask_depth_notional": _side_depth_notional(row, ASK_PX, ASK_SZ, size_multiplier),
    }


def book_at(code: str, trade_date, sample_time: str = "14:55:00",
            size_multiplier: float = 1.0,
            root: Path = SNAPSHOT_ROOT) -> dict | None:
    """Representative book for one instrument+session, or ``None`` if unrecorded."""
    frame = read_session_snapshots(code, trade_date, root)
    if frame is None:
        return None
    return book_from_row(_sample_row(frame, sample_time), size_multiplier)

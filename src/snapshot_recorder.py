"""Self-recording of real L2 snapshots for Track A.

Polls a realtime quote source during the trading session and lands the standard
depth-5 schema (:mod:`src.l2_snapshot`) to
``data/snapshots/<code>/<YYYYMMDD>.parquet``, which the Track A execution layer
consumes via ``--use-snapshots``.

Data-source reality on free A-share feeds:
  * **ETF leg** — tushare ``realtime_quote`` (sina) returns full 5-level book
    (``B1_P..B5_P`` / ``A1_P..A5_P``); recordable today.
  * **Index-futures leg** — CFFEX free realtime exposes only level-1; a real
    5-level book needs a broker CTP feed. The schema tolerates partial depth
    (missing levels are 0 and skipped in depth/notional), so a level-1 futures
    adapter can be dropped in behind the same :class:`QuoteSource` protocol.
"""

from __future__ import annotations

import time as _time
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

import pandas as pd

from src import l2_snapshot
from src.l2_snapshot import REQUIRED_COLUMNS, SNAPSHOT_ROOT, snapshot_path, validate_snapshot_frame

_DEPTH_COLS = (l2_snapshot.BID_PX + l2_snapshot.BID_SZ
               + l2_snapshot.ASK_PX + l2_snapshot.ASK_SZ)


def _f(value) -> float:
    """Coerce a feed cell (may be '', None, NaN) to a float, defaulting to 0.0."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if pd.isna(out) else out


def empty_row(ts) -> dict:
    """A schema-complete snapshot row with all levels zeroed."""
    row: dict = {"ts": pd.Timestamp(ts)}
    for col in _DEPTH_COLS:
        row[col] = 0.0
    return row


class QuoteSource(Protocol):
    """Fetch one normalized depth-5 snapshot row for an instrument code."""

    def fetch(self, code: str) -> dict:
        ...


class TushareRealtimeSource:
    """tushare ``realtime_quote`` (sina) → depth-5 row. Full book for ETF/stock.

    sina sizes are in 手 (lots); 1 手 = 100 shares for ETF/stock, so sizes are
    scaled to shares to keep ``size_multiplier=1`` notional correct downstream.
    """

    SIZE_UNIT = 100

    def __init__(self, src: str = "sina") -> None:
        self.src = src
        self._ts = None

    def _api(self):
        if self._ts is None:
            import tushare as ts
            self._ts = ts
        return self._ts

    def fetch(self, code: str) -> dict:
        df = self._api().realtime_quote(ts_code=code, src=self.src)
        return self.normalize(df.iloc[0])

    @classmethod
    def normalize(cls, r: pd.Series) -> dict:
        date, tme = str(r.get("DATE", "")), str(r.get("TIME", ""))
        ts = pd.to_datetime(f"{date} {tme}".strip()) if date else pd.Timestamp.now()
        row = {"ts": ts}
        for i in range(1, l2_snapshot.LEVELS + 1):
            row[f"bid_px_{i}"] = _f(r.get(f"B{i}_P"))
            row[f"bid_sz_{i}"] = _f(r.get(f"B{i}_V")) * cls.SIZE_UNIT
            row[f"ask_px_{i}"] = _f(r.get(f"A{i}_P"))
            row[f"ask_sz_{i}"] = _f(r.get(f"A{i}_V")) * cls.SIZE_UNIT
        return row


def append_rows(code: str, rows: list[dict],
                root: Path = SNAPSHOT_ROOT) -> dict[str, Path]:
    """Merge recorded rows into per-session parquet files (dedupe by ts).

    Rows may span sessions; each date is written to its own file. Returns a map
    of ``YYYYMMDD -> path`` for the files written.
    """
    if not rows:
        return {}
    frame = pd.DataFrame(rows)[list(REQUIRED_COLUMNS)].copy()
    frame["ts"] = pd.to_datetime(frame["ts"])
    written: dict[str, Path] = {}
    for day, group in frame.groupby(frame["ts"].dt.strftime("%Y%m%d")):
        path = snapshot_path(code, group["ts"].iloc[0], root)
        merged = group
        if path.exists():
            merged = pd.concat([pd.read_parquet(path), group], ignore_index=True)
        merged = merged.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
        validate_snapshot_frame(merged)
        path.parent.mkdir(parents=True, exist_ok=True)
        merged.to_parquet(path, index=False)
        written[day] = path
    return written


def _past_cutoff(now: datetime, until: str) -> bool:
    tod = pd.Timedelta(hours=now.hour, minutes=now.minute, seconds=now.second)
    return tod >= pd.to_timedelta(until)


def record_session(sources: dict[str, QuoteSource], *,
                   interval: float = 3.0, until: str = "15:00:05",
                   root: Path = SNAPSHOT_ROOT, max_polls: int | None = None,
                   now_fn: Callable[[], datetime] = datetime.now,
                   sleep_fn: Callable[[float], None] = _time.sleep,
                   on_poll: Callable[[str, dict | None, Exception | None], None] | None = None,
                   ) -> dict[str, dict[str, Path]]:
    """Poll ``sources`` every ``interval`` seconds until ``until`` (or max_polls).

    ``now_fn``/``sleep_fn`` are injectable for tests. A failing fetch on one code
    is reported via ``on_poll`` and skipped, never aborting the whole session.
    Returns ``{code: {YYYYMMDD: path}}`` for everything written.
    """
    buffers: dict[str, list[dict]] = {code: [] for code in sources}
    polls = 0
    # do-while: always capture at least one snapshot (one-shot validation works
    # even outside market hours), then stop on max_polls or the time cutoff.
    while True:
        for code, source in sources.items():
            try:
                row = source.fetch(code)
            except Exception as exc:  # one bad code must not kill the session
                if on_poll:
                    on_poll(code, None, exc)
                continue
            buffers[code].append(row)
            if on_poll:
                on_poll(code, row, None)
        polls += 1
        if max_polls is not None and polls >= max_polls:
            break
        if _past_cutoff(now_fn(), until):
            break
        sleep_fn(interval)

    return {code: append_rows(code, rows, root) for code, rows in buffers.items() if rows}

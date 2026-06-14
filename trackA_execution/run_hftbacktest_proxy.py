"""Track A runner: hftbacktest-style execution matching over Track 0 signals.

This is a deterministic proxy until real ETF/futures L2 snapshots are available.
It reuses the basis signal, slices each target-position change, and writes fill
quality diagnostics that match the eventual hftbacktest evaluation surface.

Run:
    python trackA_execution/run_hftbacktest_proxy.py --start 20190101 --allow-short
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import PAIRS, SIGNALS, COSTS, BACKTEST_START, BACKTEST_END, EXECUTION  # noqa: E402
from src import basis_model, data_tushare as dt  # noqa: E402
from src.hft_execution import (  # noqa: E402
    SnapshotBookProvider, SyntheticBookProvider, simulate_pair_execution,
)
from src.regime import identify_basis_regime  # noqa: E402
from src.signal import convergence_position  # noqa: E402

RESULTS = ROOT / "results"
RESULTS.mkdir(exist_ok=True)


def run_pair(pair, start: str, end: str | None, signals, book_provider,
             ) -> tuple[pd.DataFrame, dict]:
    pstart = max(start, pair.start_date)
    raw = dt.load_pair(pair, pstart, end)
    df = basis_model.with_basis_columns(raw, rf=COSTS.rf).set_index("trade_date")
    df = identify_basis_regime(df)
    pos = convergence_position(df, signals)
    fills, summary = simulate_pair_execution(df, pos, pair, EXECUTION, book_provider)
    summary["span"] = f"{df.index[0].date()}..{df.index[-1].date()}"
    summary["position_changes"] = int(pos.diff().abs().fillna(pos.abs()).astype(bool).sum())
    return fills, summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=BACKTEST_START)
    ap.add_argument("--end", default=BACKTEST_END)
    ap.add_argument("--allow-short", action="store_true",
                    help="allow reverse arbitrage orders that require short ETF availability")
    ap.add_argument("--use-snapshots", action="store_true",
                    help="drive fills from real / self-recorded L2 snapshots under "
                         "data/snapshots/<code>/<YYYYMMDD>.parquet (synthetic fallback)")
    args = ap.parse_args()

    signals = dataclasses.replace(SIGNALS, allow_short_etf=args.allow_short)
    book_provider = SnapshotBookProvider() if args.use_snapshots else SyntheticBookProvider()
    all_fills, rows = [], []
    for pair in PAIRS:
        fills, summary = run_pair(pair, args.start, args.end, signals, book_provider)
        if not fills.empty:
            all_fills.append(fills)
        rows.append(summary)

    suffix = "short" if args.allow_short else "noshort"
    summary_path = RESULTS / f"trackA_execution_summary_{suffix}.csv"
    fills_path = RESULTS / f"trackA_execution_fills_{suffix}.csv"
    pd.DataFrame(rows).to_csv(summary_path, index=False, encoding="utf-8-sig")
    if all_fills:
        pd.concat(all_fills, ignore_index=True).to_csv(
            fills_path, index=False, encoding="utf-8-sig",
        )
    else:
        pd.DataFrame().to_csv(fills_path, index=False, encoding="utf-8-sig")

    print("\nTrack A hftbacktest-style execution")
    book_mode = "L2 snapshots (synthetic fallback)" if args.use_snapshots else "synthetic book"
    print(f"book={book_mode} | allow_short_etf={args.allow_short} | "
          f"window {args.start}..{args.end or 'now'}")
    print(pd.DataFrame(rows)[[
        "pair", "orders", "child_fills", "cross_rate",
        "mean_slippage_bps", "p95_slippage_bps", "snapshot_fill_rate", "position_changes",
    ]].to_string(index=False))
    print(f"[saved] {summary_path}")
    print(f"[saved] {fills_path}")


if __name__ == "__main__":
    main()

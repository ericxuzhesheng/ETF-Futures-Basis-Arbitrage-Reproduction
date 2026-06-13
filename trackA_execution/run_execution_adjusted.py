"""Execution-adjusted Track 0 equity using Track A proxy fills.

The Track A proxy produces child-order fill slippage. This runner subtracts
that realised execution drag from the Track 0 convergence net returns so the
research output can compare theoretical versus execution-adjusted P&L.

Run:
    python trackA_execution/run_execution_adjusted.py --start 20190101 --allow-short
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
from src import basis_model, data_tushare as dt, metrics  # noqa: E402
from src.hft_execution import simulate_pair_execution  # noqa: E402
from src.regime import identify_basis_regime  # noqa: E402
from src.signal import convergence_position  # noqa: E402
from track0_empirical.run_backtest import _attach_spot_leg, _simulate  # noqa: E402

RESULTS = ROOT / "results"
RESULTS.mkdir(exist_ok=True)


def _run_pair(pair, start: str, end: str | None, signals) -> dict:
    pstart = max(start, pair.start_date)
    raw = dt.load_pair(pair, pstart, end)
    df = basis_model.with_basis_columns(raw, rf=COSTS.rf).set_index("trade_date")
    df = identify_basis_regime(df)
    df = _attach_spot_leg(df, pair, pstart, end, dynamic=False)
    pos = convergence_position(df, signals)
    theoretical = _simulate(df, pos)
    fills, _ = simulate_pair_execution(df, pos, pair, EXECUTION)
    if fills.empty:
        execution_drag = pd.Series(0.0, index=theoretical.index)
    else:
        fills = fills.copy()
        fills["drag"] = (
            fills["total_slippage_bps"] / 10_000.0
            * fills["child_notional"] / EXECUTION.target_notional
        )
        execution_drag = fills.groupby("trade_date")["drag"].sum()
        execution_drag.index = pd.to_datetime(execution_drag.index)
        execution_drag = execution_drag.reindex(theoretical.index).fillna(0.0)
    adjusted = (theoretical - execution_drag).rename(pair.name)
    return {
        "pair": pair.name,
        "theoretical": theoretical.rename(pair.name),
        "execution_drag": execution_drag.rename(pair.name),
        "adjusted": adjusted,
        "position": pos,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=BACKTEST_START)
    ap.add_argument("--end", default=BACKTEST_END)
    ap.add_argument("--allow-short", action="store_true")
    args = ap.parse_args()

    signals = dataclasses.replace(SIGNALS, allow_short_etf=args.allow_short)
    results = [_run_pair(pair, args.start, args.end, signals) for pair in PAIRS]
    theo = pd.concat([r["theoretical"] for r in results], axis=1).fillna(0.0).mean(axis=1)
    drag = pd.concat([r["execution_drag"] for r in results], axis=1).fillna(0.0).mean(axis=1)
    adj = pd.concat([r["adjusted"] for r in results], axis=1).fillna(0.0).mean(axis=1)
    active = pd.concat([r["position"].ne(0).rename(r["pair"]) for r in results], axis=1) \
        .reindex(theo.index, fill_value=False).any(axis=1).astype(int)

    mt = metrics.summarize(theo, active, rf=COSTS.rf)
    ma = metrics.summarize(adj, active, rf=COSTS.rf)
    rows = [
        {"scenario": "track0_theoretical", **mt},
        {"scenario": "trackA_execution_adjusted", **ma,
         "execution_drag_ann": mt["ann_return"] - ma["ann_return"]},
    ]
    summary = pd.DataFrame(rows)
    suffix = "short" if args.allow_short else "noshort"
    path = RESULTS / f"execution_adjusted_summary_{suffix}.csv"
    series_path = RESULTS / f"execution_adjusted_daily_{suffix}.csv"
    summary.to_csv(path, index=False, encoding="utf-8-sig")
    pd.DataFrame({
        "trade_date": theo.index,
        "theoretical": theo.values,
        "execution_drag": drag.values,
        "execution_adjusted": adj.values,
    }).to_csv(series_path, index=False, encoding="utf-8-sig")
    print(summary[["scenario", "ann_return", "sharpe", "max_dd", "win_rate"]].to_string(
        index=False,
        formatters={
            "ann_return": "{:.2%}".format,
            "max_dd": "{:.2%}".format,
            "win_rate": "{:.2%}".format,
        },
    ))
    print(f"[saved] {path}")
    print(f"[saved] {series_path}")


if __name__ == "__main__":
    main()

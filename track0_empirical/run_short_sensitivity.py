"""Short-availability and borrow-fee sensitivity for basis arbitrage.

The report-replication case assumes institutional short availability for the
ETF leg. This runner relaxes that assumption by scaling only reverse-arbitrage
positions (short ETF / long futures) and charging a borrow fee on that sleeve.

Run:
    python track0_empirical/run_short_sensitivity.py --start 20190101
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import PAIRS, SIGNALS, COSTS, BACKTEST_START, BACKTEST_END, TRADING_DAYS  # noqa: E402
from src import basis_model, data_tushare as dt, metrics  # noqa: E402
from src.regime import identify_basis_regime  # noqa: E402
from src.signal import convergence_position  # noqa: E402
from track0_empirical.run_backtest import _attach_spot_leg, _simulate  # noqa: E402

RESULTS = ROOT / "results"
RESULTS.mkdir(exist_ok=True)

SHORT_AVAILABILITY = (0.0, 0.25, 0.50, 0.75, 1.0)
BORROW_FEES = (0.00, 0.03, 0.06, 0.08, 0.10)


def _pair_frame(pair, start: str, end: str | None) -> pd.DataFrame:
    pstart = max(start, pair.start_date)
    raw = dt.load_pair(pair, pstart, end)
    df = basis_model.with_basis_columns(raw, rf=COSTS.rf).set_index("trade_date")
    df = identify_basis_regime(df)
    return _attach_spot_leg(df, pair, pstart, end, dynamic=False)


def _scaled_reverse_position(pos: pd.Series, availability: float) -> pd.Series:
    out = pos.astype(float).copy()
    out[out < 0] = out[out < 0] * availability
    return out


def run_grid(start: str, end: str | None) -> pd.DataFrame:
    signals = dataclasses.replace(SIGNALS, allow_short_etf=True)
    pair_frames = {pair.name: _pair_frame(pair, start, end) for pair in PAIRS}
    base_positions = {
        pair.name: convergence_position(pair_frames[pair.name], signals)
        for pair in PAIRS
    }

    rows = []
    for availability in SHORT_AVAILABILITY:
        for borrow_fee in BORROW_FEES:
            pair_nets = []
            pair_active = []
            for pair in PAIRS:
                df = pair_frames[pair.name]
                pos = _scaled_reverse_position(base_positions[pair.name], availability)
                net = _simulate(df, pos)
                borrow_drag = (pos.shift(1).fillna(0).clip(upper=0).abs()
                               * borrow_fee / TRADING_DAYS)
                net = (net - borrow_drag).rename(pair.name)
                pair_nets.append(net)
                pair_active.append(pos.ne(0).rename(pair.name))

            composite = pd.concat(pair_nets, axis=1).fillna(0.0).mean(axis=1)
            active = pd.concat(pair_active, axis=1).reindex(composite.index, fill_value=False) \
                .any(axis=1).astype(int)
            m = metrics.summarize(composite, active, rf=COSTS.rf)
            rows.append({
                "short_availability": availability,
                "borrow_fee": borrow_fee,
                **m,
            })
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=BACKTEST_START)
    ap.add_argument("--end", default=BACKTEST_END)
    args = ap.parse_args()

    out = run_grid(args.start, args.end)
    path = RESULTS / "short_sensitivity.csv"
    out.to_csv(path, index=False, encoding="utf-8-sig")
    print(out[[
        "short_availability", "borrow_fee", "ann_return",
        "sharpe", "max_dd", "win_rate",
    ]].to_string(index=False, formatters={
        "ann_return": "{:.2%}".format,
        "borrow_fee": "{:.2%}".format,
        "max_dd": "{:.2%}".format,
        "win_rate": "{:.2%}".format,
    }))
    print(f"[saved] {path}")


if __name__ == "__main__":
    main()

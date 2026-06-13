"""Track 0 — empirical reproduction of the ETF / index-futures basis arbitrage.

For each (futures, ETF) pair we:
  1. pull spot index / continuous main futures / ETF from tushare (cached),
  2. build the annualised basis rate (carry model),
  3. generate positions for two strategy variants:
       - galaxy   : rolling historical-percentile threshold (银河期货)
       - orient   : z-score mean reversion (东证期货)
  4. simulate the notional-neutral hedged spread net of costs,
  5. report annualised return / sharpe / max-dd / win-rate / holding period,
     side by side with the published sell-side numbers.

A 银河-style multi-variety composite (equal weight) is also produced.

Run:  python track0_empirical/run_backtest.py
      python track0_empirical/run_backtest.py --start 20180101 --end 20240401
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import (  # noqa: E402
    PAIRS, COSTS, SIGNALS, BACKTEST_START, BACKTEST_END, TRADING_DAYS,
    REPORT_CONVERGENCE, REPORT_MEANREV, REPORT_GALAXY_COMPOSITE,
)
from src import data_tushare as dt  # noqa: E402
from src import basis_model, metrics  # noqa: E402
from src.signal import (  # noqa: E402
    convergence_position, galaxy_percentile_position, orient_zscore_position,
)

RESULTS = ROOT / "results"
RESULTS.mkdir(exist_ok=True)

_OPEN_COST = COSTS.fut_fee + COSTS.etf_fee + COSTS.impact  # one direction, both legs


def _tracking_excess(df: pd.DataFrame) -> pd.Series:
    """ETF total return minus its index return = 折溢价/分红 excess captured by
    holding the ETF leg (small, but the report's '固定ETF叠加折溢价' alpha)."""
    r_etf = df["etf_adj_close"].pct_change()
    r_idx = df["spot"].pct_change()
    return (r_etf - r_idx).fillna(0.0)


def _simulate(df: pd.DataFrame, pos: pd.Series) -> pd.Series:
    """Net daily strategy return on deployed capital (carry model).

    期现套利 earns the *annualised basis rate* as carry while correctly
    positioned (held to convergence/delivery → 报告 near-100% win, tiny DD):
        +1 长ETF/空期货 in 升水(basis_rate>0)  -> +basis_rate/yr
        -1 空ETF/多期货 in 贴水(basis_rate<0)  -> -(neg)=+ |basis_rate|/yr
    Idle capital earns rf (银河: 闲置保证金年化2%). ETF leg also picks up the
    small 折溢价/分红 excess. Costs charged on each position change.
    """
    pos_exec = pos.shift(1).fillna(0)
    # Daily carry = annualised basis rate / trading days, earned while correctly
    # positioned. dte floored at 7 calendar days so the annualisation does not
    # explode/flip sign in the last week before delivery.
    rate = df["basis"] / df["spot"] * 365.0 / df["dte"].clip(lower=7)
    daily_carry = pos_exec * rate / TRADING_DAYS
    idle = (pos_exec == 0).astype(float) * (COSTS.rf / TRADING_DAYS)
    track = pos_exec.abs() * _tracking_excess(df)
    dpos = pos.diff().abs().fillna(pos.abs())
    cost = dpos * _OPEN_COST
    return (daily_carry + idle + track - cost).fillna(0.0).rename("net_ret")


def run_pair(pair, start: str, end: str | None, signals) -> dict:
    pstart = max(start, pair.start_date)
    raw = dt.load_pair(pair, pstart, end)
    df = basis_model.with_basis_columns(raw, rf=COSTS.rf).set_index("trade_date")

    results = {"pair": pair.name, "rows": len(df),
               "span": f"{df.index[0].date()}..{df.index[-1].date()}"}
    nets = {}
    for label, gen in (("conv", convergence_position),
                       ("galaxy", galaxy_percentile_position),
                       ("orient", orient_zscore_position)):
        pos = gen(df, signals)
        net = _simulate(df, pos)
        nets[label] = net
        m = metrics.summarize(net, pos.shift(1).fillna(0), rf=COSTS.rf)
        results[label] = m
    results["_nets"] = nets  # kept for composite, stripped before JSON dump
    return results


def build_composite(per_pair: list[dict]) -> dict:
    """银河式多品种复合: equal-weight average of per-pair convergence net returns."""
    series = [r["_nets"]["conv"] for r in per_pair]
    aligned = pd.concat(series, axis=1).fillna(0.0)
    composite = aligned.mean(axis=1)
    # exposure-style position proxy: nonzero if any leg active
    active = (pd.concat([(s != 0) for s in series], axis=1).fillna(False)
              .any(axis=1).astype(int))
    return metrics.summarize(composite, active, rf=COSTS.rf)


def _fmt_pct(x: float) -> str:
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x*100:6.2f}%"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=BACKTEST_START)
    ap.add_argument("--end", default=BACKTEST_END)
    ap.add_argument("--allow-short", action="store_true",
                    help="允许做空ETF(反向套利). 复现报告需开启; 默认关闭=A股融券受限现实")
    args = ap.parse_args()

    signals = dataclasses.replace(SIGNALS, allow_short_etf=args.allow_short)
    mode = "融券开启 (复现报告/有融券资源机构)" if args.allow_short \
        else "融券受限 (A股现实, 仅正向套利)"
    per_pair = [run_pair(p, args.start, args.end, signals) for p in PAIRS]

    # ---- console table -----------------------------------------------------
    print("\n" + "=" * 92)
    print("Track 0 — ETF / 股指期货 期现基差套利复现  (net of costs)")
    print(f"模式: {mode}   区间: {args.start}..{args.end or 'now'}")
    print("=" * 92)
    hdr = f"{'pair':12s} {'strat':8s} {'ann_ret':>9s} {'sharpe':>7s} {'max_dd':>8s} {'win':>7s} {'trades':>7s} {'hold':>6s}  vs报告"
    print(hdr)
    print("-" * 92)
    rows_out = []
    for r in per_pair:
        for strat, report in (("conv", REPORT_CONVERGENCE.get(r["pair"], {}).get("ann_return")),
                              ("orient", REPORT_MEANREV.get(r["pair"]))):
            m = r[strat]
            ref = _fmt_pct(report) if report is not None else "  -  "
            print(f"{r['pair']:12s} {strat:8s} {_fmt_pct(m['ann_return']):>9s} "
                  f"{m['sharpe']:7.2f} {_fmt_pct(m['max_dd']):>8s} {_fmt_pct(m['win_rate']):>7s} "
                  f"{m['n_trades']:7d} {m['avg_hold_days']:6.1f}  {ref}")
            rows_out.append({"pair": r["pair"], "strategy": strat, "span": r["span"],
                             **{k: m[k] for k in
                                ("ann_return", "ann_vol", "sharpe", "max_dd",
                                 "win_rate", "n_trades", "avg_hold_days", "exposure")},
                             "report_ann_return": report})

    comp = build_composite(per_pair)
    rep = REPORT_GALAXY_COMPOSITE
    print("-" * 92)
    print(f"{'COMPOSITE':12s} {'galaxy':8s} {_fmt_pct(comp['ann_return']):>9s} "
          f"{comp['sharpe']:7.2f} {_fmt_pct(comp['max_dd']):>8s} {_fmt_pct(comp['win_rate']):>7s} "
          f"{comp['n_trades']:7d} {comp['avg_hold_days']:6.1f}  "
          f"报告:{_fmt_pct(rep['ann_return'])}/DD{_fmt_pct(rep['max_dd'])}/SR{rep['sharpe']}")
    print("=" * 92)
    print("说明: conv≈基差收敛(银河/东证), orient≈东证z-score均值回归; 报告列为对照基准。")
    print(f"      当前模式 allow_short_etf={args.allow_short}。融券受限时仅能做正向(升水)套利。\n")

    rows_out.append({"pair": "COMPOSITE", "strategy": "galaxy", "span": "",
                     **{k: comp[k] for k in
                        ("ann_return", "ann_vol", "sharpe", "max_dd",
                         "win_rate", "n_trades", "avg_hold_days", "exposure")},
                     "report_ann_return": rep["ann_return"]})

    out = pd.DataFrame(rows_out)
    suffix = "short" if args.allow_short else "noshort"
    csv_path = RESULTS / f"basis_summary_{suffix}.csv"
    out.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"[saved] {csv_path}")


if __name__ == "__main__":
    main()

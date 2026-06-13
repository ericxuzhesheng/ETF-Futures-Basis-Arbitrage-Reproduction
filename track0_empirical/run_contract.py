"""Track 0 (rigorous) — 逐合约持有至交割 per-contract backtest runner.

Runs the per-contract hold-to-delivery accounting for all four pairs, builds the
银河式 multi-variety composite, and prints results side by side with the published
report numbers. This is the honest, roll-jump-free, terminal-convergence version
of Track 0 (vs. the continuous-main locked-carry approximation in run_backtest.py).

Run:  python track0_empirical/run_contract.py --allow-short
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import (PAIRS, COSTS, SIGNALS, BACKTEST_START, BACKTEST_END,  # noqa: E402
                    REPORT_CONVERGENCE, REPORT_GALAXY_COMPOSITE)
from src import metrics, contract_backtest  # noqa: E402

RESULTS = ROOT / "results"
RESULTS.mkdir(exist_ok=True)


def _fmt(x) -> str:
    return "n/a" if x is None or (isinstance(x, float) and pd.isna(x)) else f"{x*100:6.2f}%"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=BACKTEST_START)
    ap.add_argument("--end", default=BACKTEST_END)
    ap.add_argument("--allow-short", action="store_true",
                    help="允许做空ETF(反向套利). 复现报告需开启")
    args = ap.parse_args()

    signals = dataclasses.replace(SIGNALS, allow_short_etf=args.allow_short)
    mode = "融券开启 (复现报告)" if args.allow_short else "融券受限 (A股现实)"

    results = [contract_backtest.run_pair(p, args.start, args.end, signals) for p in PAIRS]

    print("\n" + "=" * 90)
    print("Track 0 (逐合约持有至交割) — ETF / 股指期货 期现基差套利")
    print(f"模式: {mode}   区间: {args.start}..{args.end or 'now'}")
    print("=" * 90)
    print(f"{'pair':12s} {'ann_ret':>9s} {'sharpe':>7s} {'max_dd':>8s} {'win':>7s} "
          f"{'trades':>7s} {'hold':>6s}  vs报告")
    print("-" * 90)
    rows, nets = [], []
    for r in results:
        net = r["net"]
        nets.append(net)
        active = (net != 0).astype(int)
        m = metrics.summarize(net, active, rf=COSTS.rf)
        rep = REPORT_CONVERGENCE.get(r["pair"], {}).get("ann_return")
        print(f"{r['pair']:12s} {_fmt(m['ann_return']):>9s} {m['sharpe']:7.2f} "
              f"{_fmt(m['max_dd']):>8s} {_fmt(m['win_rate']):>7s} {r['n_trades']:7d} "
              f"{r['avg_hold']:6.1f}  {_fmt(rep)}")
        rows.append({"pair": r["pair"], **{k: m[k] for k in
                     ("ann_return", "ann_vol", "sharpe", "max_dd", "win_rate")},
                     "n_trades": r["n_trades"], "avg_hold": r["avg_hold"],
                     "report_ann_return": rep})

    composite = pd.concat(nets, axis=1).fillna(0.0).mean(axis=1)
    cm = metrics.summarize(composite, (composite != 0).astype(int), rf=COSTS.rf)
    rep = REPORT_GALAXY_COMPOSITE
    print("-" * 90)
    print(f"{'COMPOSITE':12s} {_fmt(cm['ann_return']):>9s} {cm['sharpe']:7.2f} "
          f"{_fmt(cm['max_dd']):>8s} {_fmt(cm['win_rate']):>7s} {'':>7s} {'':>6s}  "
          f"报告:{_fmt(rep['ann_return'])}/DD{_fmt(rep['max_dd'])}/SR{rep['sharpe']}")
    print("=" * 90)
    print("逐合约: 在单一合约内mark-to-market持有至交割, 无连续主力换月跳价; ")
    print("        盈亏含真实路径波动(回撤更真实), 终值在交割收敛。\n")

    rows.append({"pair": "COMPOSITE", **{k: cm[k] for k in
                 ("ann_return", "ann_vol", "sharpe", "max_dd", "win_rate")},
                 "n_trades": "", "avg_hold": "", "report_ann_return": rep["ann_return"]})
    suffix = "short" if args.allow_short else "noshort"
    path = RESULTS / f"contract_summary_{suffix}.csv"
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[saved] {path}")


if __name__ == "__main__":
    main()

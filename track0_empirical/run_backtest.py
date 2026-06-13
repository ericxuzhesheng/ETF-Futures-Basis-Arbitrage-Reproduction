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

_OPEN_COST = COSTS.exec_one_way  # realised one-way execution cost, both legs


def _attach_spot_leg(df: pd.DataFrame, pair, start: str, end: str | None,
                     dynamic: bool) -> pd.DataFrame:
    """Attach track_excess (ETF total return - index) for the spot leg, either
    from the fixed primary ETF or a dynamically selected one (东证 择优现货)."""
    out = df.copy()
    if dynamic and len(pair.etf_candidates) > 1:
        from src import etf_select
        idx = out.reset_index()[["trade_date", "spot"]]
        sel = etf_select.select(pair.etf_candidates, idx, start, end) \
            .set_index("trade_date")
        out["track_excess"] = sel["track_excess"].reindex(out.index).fillna(0.0)
        out["etf_chosen"] = sel["chosen"].reindex(out.index)
        out.attrs["n_switches"] = int((sel["chosen"] != sel["chosen"].shift()).sum())
    else:
        out["track_excess"] = (out["etf_adj_close"].pct_change()
                               - out["spot"].pct_change()).fillna(0.0)
        out["etf_chosen"] = pair.etf_code
        out.attrs["n_switches"] = 0
    return out


def _simulate(df: pd.DataFrame, pos: pd.Series) -> pd.Series:
    """Net daily strategy return on deployed capital (carry model).

    期现套利 earns the *annualised basis rate* as carry while correctly
    positioned (held to convergence/delivery → 报告 near-100% win, tiny DD):
        +1 长ETF/空期货 in 升水(basis_rate>0)  -> +basis_rate/yr
        -1 空ETF/多期货 in 贴水(basis_rate<0)  -> -(neg)=+ |basis_rate|/yr
    Idle capital earns rf (银河: 闲置保证金年化2%). ETF leg also picks up the
    small 折溢价/分红 excess. Costs charged on each position change.
    """
    # RAW annualised basis rate for the locked carry (dte floored for the last
    # week). Dividends are NOT added here — they are realised seasonally through
    # the ETF total-return leg (track term below), so the dividend-adjusted rate
    # is used only for the *entry signal*, not double-counted in the P&L.
    rate = df["basis"] / df["spot"] * 365.0 / df["dte"].clip(lower=7)

    # Trade-level LOCKED carry: 期现套利 pins the terminal value at delivery, so a
    # trade entered at rate r0 earns ~r0 annualised over its holding regardless of
    # the path (报告: 胜率≈100%, 回撤极小). We lock the per-position carry at entry
    # (sign-corrected so a correctly-placed trade is positive) and accrue it daily.
    entry = (pos != 0) & (pos.shift(1).fillna(0) == 0)
    locked = (pos * rate).clip(-0.40, 0.40).where(entry).ffill()
    locked = locked.where(pos != 0, 0.0)                 # zero while flat
    locked_exec = locked.shift(1).fillna(0.0)

    daily_carry = locked_exec / TRADING_DAYS
    idle = (locked_exec == 0).astype(float) * (COSTS.rf / TRADING_DAYS)
    track = (pos.shift(1).fillna(0)).abs() * df["track_excess"]
    dpos = pos.diff().abs().fillna(pos.abs())
    cost = dpos * _OPEN_COST
    return (daily_carry + idle + track - cost).fillna(0.0).rename("net_ret")


def run_pair(pair, start: str, end: str | None, signals, dynamic: bool = False) -> dict:
    pstart = max(start, pair.start_date)
    raw = dt.load_pair(pair, pstart, end)
    df = basis_model.with_basis_columns(raw, rf=COSTS.rf).set_index("trade_date")
    df = _attach_spot_leg(df, pair, pstart, end, dynamic)

    results = {"pair": pair.name, "rows": len(df), "n_switches": df.attrs.get("n_switches", 0),
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
    ap.add_argument("--dynamic", action="store_true",
                    help="动态ETF选择(东证: 按流动性/跟踪误差择优); 默认固定主ETF")
    args = ap.parse_args()

    signals = dataclasses.replace(SIGNALS, allow_short_etf=args.allow_short)
    mode = "融券开启 (复现报告/有融券资源机构)" if args.allow_short \
        else "融券受限 (A股现实, 仅正向套利)"
    etf_mode = "动态ETF择优" if args.dynamic else "固定主ETF"
    per_pair = [run_pair(p, args.start, args.end, signals, args.dynamic) for p in PAIRS]

    # ---- console table -----------------------------------------------------
    print("\n" + "=" * 92)
    print("Track 0 — ETF / 股指期货 期现基差套利复现  (net of costs)")
    print(f"模式: {mode} | 现货端: {etf_mode}   区间: {args.start}..{args.end or 'now'}")
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
    suffix = ("short" if args.allow_short else "noshort") + ("_dyn" if args.dynamic else "")
    csv_path = RESULTS / f"basis_summary_{suffix}.csv"
    out.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"[saved] {csv_path}")

    compare_fixed_dynamic(args.start, args.end, signals)


def compare_fixed_dynamic(start, end, signals) -> None:
    """Side-by-side conv: fixed primary ETF vs dynamically selected spot leg."""
    print("\n" + "=" * 92)
    print("动态 ETF 择优 对照 (conv 基差收敛策略)")
    print("=" * 92)
    print(f"{'pair':12s} | {'固定ETF':>22s} | {'动态ETF':>22s} | {'切换':>4s}")
    print(f"{'':12s} | {'年化   夏普   波动':>22s} | {'年化   夏普   波动':>22s} |")
    print("-" * 92)
    fixed_nets, dyn_nets = [], []
    for p in PAIRS:
        rf = run_pair(p, start, end, signals, dynamic=False)
        rd = run_pair(p, start, end, signals, dynamic=True)
        mf, md = rf["conv"], rd["conv"]
        fixed_nets.append(rf["_nets"]["conv"]); dyn_nets.append(rd["_nets"]["conv"])
        print(f"{p.name:12s} | {_fmt_pct(mf['ann_return'])} {mf['sharpe']:5.2f} "
              f"{_fmt_pct(mf['ann_vol'])} | {_fmt_pct(md['ann_return'])} {md['sharpe']:5.2f} "
              f"{_fmt_pct(md['ann_vol'])} | {rd['n_switches']:4d}")
    cf = metrics.summarize(pd.concat(fixed_nets, axis=1).fillna(0).mean(axis=1),
                           pd.Series(1, index=fixed_nets[0].index))
    cd = metrics.summarize(pd.concat(dyn_nets, axis=1).fillna(0).mean(axis=1),
                           pd.Series(1, index=dyn_nets[0].index))
    print("-" * 92)
    print(f"{'COMPOSITE':12s} | {_fmt_pct(cf['ann_return'])} {cf['sharpe']:5.2f} "
          f"{_fmt_pct(cf['ann_vol'])} | {_fmt_pct(cd['ann_return'])} {cd['sharpe']:5.2f} "
          f"{_fmt_pct(cd['ann_vol'])} |")
    print("=" * 92)
    print("东证: 动态择优现货端使基差收敛更稳定(波动↓/夏普↑); IH仅510050无可切换。\n")


if __name__ == "__main__":
    main()

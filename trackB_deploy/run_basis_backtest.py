"""Track B — Binance 现货–永续 基差套利回测 (ETF–股指期货 的加密类比).

复用 Track 0 的三种因果信号 (conv / galaxy / orient) 与 regime 判定,但盘口/carry
换成可自由获取的币安现货+永续+资金费 (免密钥公共行情)。多现货/空永续吃资金费,
P&L 用真实 (现货收益 − 永续收益 + 资金费) 逐日实现, 无锁定 carry 假设。

Run:
    python trackB_deploy/run_basis_backtest.py
    python trackB_deploy/run_basis_backtest.py --start 20210101 --allow-short
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import (  # noqa: E402
    CRYPTO_PAIRS, CRYPTO_COSTS, SIGNALS, BACKTEST_START_CRYPTO, BACKTEST_END,
)
from src import crypto_backtest, data_binance as bn  # noqa: E402
from src.regime import identify_basis_regime  # noqa: E402
from src.signal import (  # noqa: E402
    convergence_position, galaxy_percentile_position, orient_zscore_position,
)

RESULTS = ROOT / "results"
RESULTS.mkdir(exist_ok=True)


def run_pair(pair, start: str, end: str | None, signals) -> dict:
    raw = bn.load_crypto_pair(pair, start, end)
    df = identify_basis_regime(raw.set_index("trade_date"))
    res = {"pair": pair.name, "rows": len(df),
           "span": f"{df.index[0].date()}..{df.index[-1].date()}"}
    nets = {}
    for label, gen in (("conv", convergence_position),
                       ("galaxy", galaxy_percentile_position),
                       ("orient", orient_zscore_position)):
        pos = gen(df, signals)
        net = crypto_backtest.simulate(df, pos, CRYPTO_COSTS)
        nets[label] = net
        res[label] = crypto_backtest.summarize(net, pos.shift(1).fillna(0), rf=CRYPTO_COSTS.rf)
    res["_nets"] = nets
    return res


def build_composite(per_pair: list[dict]) -> dict:
    series = [r["_nets"]["conv"] for r in per_pair]
    aligned = pd.concat(series, axis=1).fillna(0.0)
    composite = aligned.mean(axis=1)
    active = (pd.concat([(s != 0) for s in series], axis=1)
              .reindex(aligned.index, fill_value=False).any(axis=1).astype(int))
    return crypto_backtest.summarize(composite, active, rf=CRYPTO_COSTS.rf)


def _fmt_pct(x) -> str:
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x*100:7.2f}%"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=BACKTEST_START_CRYPTO)
    ap.add_argument("--end", default=BACKTEST_END)
    ap.add_argument("--allow-short", action="store_true",
                    help="允许做空现货腿 (吃负资金费); 默认仅做多现货/空永续 (正carry)")
    args = ap.parse_args()

    signals = dataclasses.replace(SIGNALS, allow_short_etf=args.allow_short)
    per_pair = [run_pair(p, args.start, args.end, signals) for p in CRYPTO_PAIRS]

    print("\n" + "=" * 90)
    print("Track B — Binance 现货–永续 基差套利 (净值, 费后)  ETF–股指期货 加密类比")
    mode = "双向 (含负资金费做空现货)" if args.allow_short else "仅正carry (多现货/空永续)"
    print(f"模式: {mode}   区间: {args.start}..{args.end or 'now'}")
    print("=" * 90)
    hdr = (f"{'pair':10s} {'strat':8s} {'ann_ret':>9s} {'sharpe':>7s} "
           f"{'max_dd':>9s} {'win':>8s} {'trades':>7s} {'hold':>6s}")
    print(hdr)
    print("-" * 90)
    rows_out = []
    for r in per_pair:
        for strat in ("conv", "galaxy", "orient"):
            m = r[strat]
            print(f"{r['pair']:10s} {strat:8s} {_fmt_pct(m['ann_return']):>9s} "
                  f"{m['sharpe']:7.2f} {_fmt_pct(m['max_dd']):>9s} {_fmt_pct(m['win_rate']):>8s} "
                  f"{m['n_trades']:7d} {m['avg_hold_days']:6.1f}")
            rows_out.append({"pair": r["pair"], "strategy": strat, "span": r["span"],
                             **{k: m[k] for k in
                                ("ann_return", "ann_vol", "sharpe", "max_dd",
                                 "win_rate", "n_trades", "avg_hold_days", "exposure")}})

    comp = build_composite(per_pair)
    print("-" * 90)
    print(f"{'COMPOSITE':10s} {'conv':8s} {_fmt_pct(comp['ann_return']):>9s} "
          f"{comp['sharpe']:7.2f} {_fmt_pct(comp['max_dd']):>9s} {_fmt_pct(comp['win_rate']):>8s} "
          f"{comp['n_trades']:7d} {comp['avg_hold_days']:6.1f}")
    print("=" * 90)
    print("说明: 永续 carry = 资金费; 多现货/空永续在正资金费期吃费, 即期现正向套利的加密类比。")
    rows_out.append({"pair": "COMPOSITE", "strategy": "conv", "span": "",
                     **{k: comp[k] for k in
                        ("ann_return", "ann_vol", "sharpe", "max_dd",
                         "win_rate", "n_trades", "avg_hold_days", "exposure")}})

    suffix = "short" if args.allow_short else "long"
    csv_path = RESULTS / f"trackB_basis_summary_{suffix}.csv"
    pd.DataFrame(rows_out).to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"[saved] {csv_path}")


if __name__ == "__main__":
    main()

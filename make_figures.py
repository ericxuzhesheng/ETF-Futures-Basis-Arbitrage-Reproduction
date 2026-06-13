"""Generate report figures from the Track 0 backtest.

  fig1_composite_equity.png   — 多品种复合净值 (融券on vs off)
  fig2_pair_equity.png        — 四品种 conv 净值曲线
  fig3_basis_divadj.png       — IF 年化基差: 原始 vs 分红调整
  fig5_execution_adjusted.png — Track 0 理论净值 vs Track A 执行后净值

Run:  python make_figures.py
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config import PAIRS, SIGNALS  # noqa: E402
from src import data_tushare as dt  # noqa: E402
from src import basis_model, metrics  # noqa: E402
from track0_empirical.run_backtest import run_pair, build_composite  # noqa: E402
from src import contract_backtest  # noqa: E402

FIG = ROOT / "figures"
FIG.mkdir(exist_ok=True)

# Chinese font (Windows). Falls back silently if unavailable.
for f in ("Microsoft YaHei", "SimHei", "DejaVu Sans"):
    matplotlib.rcParams["font.sans-serif"] = [f]
    break
matplotlib.rcParams["axes.unicode_minus"] = False
START = "20190101"


def _eq(net: pd.Series) -> pd.Series:
    return (1 + net.fillna(0)).cumprod()


def main() -> None:
    sig_short = dataclasses.replace(SIGNALS, allow_short_etf=True)
    sig_noshort = dataclasses.replace(SIGNALS, allow_short_etf=False)
    pp_short = [run_pair(p, START, None, sig_short) for p in PAIRS]
    pp_noshort = [run_pair(p, START, None, sig_noshort) for p in PAIRS]

    # fig1 — composite equity, two scenarios
    comp_s = pd.concat([r["_nets"]["conv"] for r in pp_short], axis=1).fillna(0).mean(axis=1)
    comp_n = pd.concat([r["_nets"]["conv"] for r in pp_noshort], axis=1).fillna(0).mean(axis=1)
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot(_eq(comp_s).index, _eq(comp_s).values, lw=1.8, color="#2F73C9",
            label="融券开启 (复现报告)")
    ax.plot(_eq(comp_n).index, _eq(comp_n).values, lw=1.8, color="#E84D3D",
            label="融券受限 (A股现实)")
    ax.axhline(1.0, color="grey", lw=0.6, ls="--")
    ax.set_title("多品种复合 期现基差套利 净值 (2019–2026)")
    ax.set_ylabel("净值"); ax.legend(); ax.grid(alpha=0.25)
    fig.tight_layout(); fig.savefig(FIG / "fig1_composite_equity.png", dpi=150)
    plt.close(fig)

    # fig2 — per-pair conv equity (short mode)
    fig, ax = plt.subplots(figsize=(9, 4.5))
    colors = ["#2F73C9", "#E84D3D", "#27AE60", "#8E44AD"]
    for r, c in zip(pp_short, colors):
        eq = _eq(r["_nets"]["conv"])
        ax.plot(eq.index, eq.values, lw=1.5, color=c,
                label=f"{r['pair']} ({metrics.ann_return(r['_nets']['conv'])*100:.1f}%)")
    ax.axhline(1.0, color="grey", lw=0.6, ls="--")
    ax.set_title("四品种 基差收敛策略 净值 (融券开启)")
    ax.set_ylabel("净值"); ax.legend(); ax.grid(alpha=0.25)
    fig.tight_layout(); fig.savefig(FIG / "fig2_pair_equity.png", dpi=150)
    plt.close(fig)

    # fig3 — IF basis: raw vs dividend-adjusted
    pIF = [p for p in PAIRS if p.name == "IF+300ETF"][0]
    raw = dt.load_pair(pIF, START, None)
    d = basis_model.with_basis_columns(raw, rf=0.02).set_index("trade_date")
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot(d.index, d["basis_rate_raw"] * 100, lw=0.9, color="#999999", label="原始 (F−S)")
    ax.plot(d.index, d["basis_rate"] * 100, lw=1.1, color="#2F73C9", label="分红调整后")
    ax.axhline(0, color="black", lw=0.6)
    ax.set_title("IF 年化基差率: 分红调整前后 (沪深300, 股息≈2.5%)")
    ax.set_ylabel("年化基差率 (%)"); ax.legend(); ax.grid(alpha=0.25)
    ax.set_ylim(-25, 15)
    fig.tight_layout(); fig.savefig(FIG / "fig3_basis_divadj.png", dpi=150)
    plt.close(fig)

    # fig4 — accounting comparison: locked-carry (continuous main) vs per-contract
    sig = dataclasses.replace(SIGNALS, allow_short_etf=True)
    locked = pd.concat([run_pair(p, START, None, sig)["_nets"]["conv"] for p in PAIRS],
                       axis=1).fillna(0).mean(axis=1)
    perc = pd.concat([contract_backtest.run_pair(p, START, None, sig)["net"] for p in PAIRS],
                     axis=1).fillna(0).mean(axis=1)
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot(_eq(locked).index, _eq(locked).values, lw=1.8, color="#E84D3D",
            label="连续主力 + 锁定carry (近似)")
    ax.plot(_eq(perc).index, _eq(perc).values, lw=1.8, color="#2F73C9",
            label="逐合约持有至交割 (严格)")
    ax.axhline(1.0, color="grey", lw=0.6, ls="--")
    ax.set_title("记账口径对照: 锁定carry近似 vs 逐合约严格 (复合, 融券开启)")
    ax.set_ylabel("净值"); ax.legend(); ax.grid(alpha=0.25)
    fig.tight_layout(); fig.savefig(FIG / "fig4_accounting_compare.png", dpi=150)
    plt.close(fig)

    # fig5 — execution-adjusted equity if Track A output exists
    exec_path = ROOT / "results" / "execution_adjusted_daily_short.csv"
    if exec_path.exists():
        ex = pd.read_csv(exec_path, parse_dates=["trade_date"]).set_index("trade_date")
        fig, ax = plt.subplots(figsize=(9, 4.5))
        ax.plot(_eq(ex["theoretical"]).index, _eq(ex["theoretical"]).values,
                lw=1.8, color="#2F73C9", label="Track 0 理论净值")
        ax.plot(_eq(ex["execution_adjusted"]).index, _eq(ex["execution_adjusted"]).values,
                lw=1.8, color="#E84D3D", label="Track A 执行后净值")
        ax.axhline(1.0, color="grey", lw=0.6, ls="--")
        ax.set_title("执行成本回灌: 理论净值 vs 执行后净值 (复合, 融券开启)")
        ax.set_ylabel("净值"); ax.legend(); ax.grid(alpha=0.25)
        fig.tight_layout(); fig.savefig(FIG / "fig5_execution_adjusted.png", dpi=150)
        plt.close(fig)

    print("[saved] fig1..fig5 (composite / pair / basis / accounting / execution-adjusted)")


if __name__ == "__main__":
    main()

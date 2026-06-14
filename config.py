"""Single source of truth for the ETF–index-futures basis-arbitrage reproduction.

Reproduces the strategy framework from three Chinese sell-side reports:
  * 东证期货 — 股指期货与ETF的基差套利 (基差收敛 + 均值回归)
  * 银河期货 — ETF期现套利策略 (年化基差率历史分位数阈值, 多品种复合)
  * 华泰柏瑞 — 沪深300ETF期现套利 (持有成本模型 / 无套利区间)

All tunables live here so the backtest, signals and report read one source.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Pair:
    """One index-futures / ETF arbitrage pair."""

    name: str          # human label, e.g. "IF+300ETF"
    fut_code: str      # continuous main-contract handle for fut_mapping, e.g. "IF.CFX"
    index_code: str    # underlying index (futures settles to this), e.g. "000300.SH"
    tr_code: str       # total-return (全收益) index, gap vs price = dividends
    etf_candidates: tuple[str, ...]  # ETF legs; [0] = primary (fixed mode)
    multiplier: int    # contract multiplier (point value, CNY/index-point)
    start_date: str    # earliest sensible backtest start (contract listing aware)

    @property
    def etf_code(self) -> str:
        return self.etf_candidates[0]


# The four CFFEX index-futures families and their candidate broad-based ETF legs.
PAIRS: tuple[Pair, ...] = (
    Pair("IH+50ETF",   "IH.CFX", "000016.SH", "H00016.CSI",
         ("510050.SH",), 300, "20190101"),
    Pair("IF+300ETF",  "IF.CFX", "000300.SH", "H00300.CSI",
         ("510300.SH", "510330.SH", "510310.SH", "159919.SZ"), 300, "20190101"),
    Pair("IC+500ETF",  "IC.CFX", "000905.SH", "H00905.CSI",
         ("510500.SH", "512500.SH", "159922.SZ"), 200, "20190101"),
    Pair("IM+1000ETF", "IM.CFX", "000852.SH", "H00852.CSI",
         ("512100.SH", "159845.SZ", "560010.SH"), 200, "20220722"),  # IM listed 2022-07-22
)

# Rolling lookback (trading days) for the trailing dividend-yield estimate used
# to dividend-adjust the basis (分红基差调整).
DIV_YIELD_WINDOW = 244

# Dynamic ETF selection (东证: 按流动性/跟踪误差/折溢价择优现货端).
ETF_REBALANCE_DAYS = 21    # monthly rebalance, avoids daily churn
ETF_TE_WINDOW = 60         # tracking-error lookback (trading days)
ETF_LIQ_WINDOW = 20        # liquidity (amount) lookback

# Per-contract hold-to-delivery accounting (逐合约持有至交割).
# Enter ~a month out (front contract with dte>=floor) where the basis is still
# meaningful, then hold to near delivery — mirrors 报告 ~40 天持仓。
CONTRACT_ENTRY_MIN_DTE = 25  # open in the near contract ~25-45 days from delivery
CONTRACT_ROLL_BUFFER = 3     # close the trade when within N days of delivery

# Default backtest window (overridable by CLI). End None -> latest available.
BACKTEST_START = "20190101"
BACKTEST_END: str | None = None


@dataclass(frozen=True)
class Costs:
    """Round-trip transaction & carry costs (华泰柏瑞 / 银河 口径, single-side rates)."""

    fut_fee: float = 0.00015      # 期货手续费 单边
    etf_fee: float = 0.0005       # ETF手续费 单边
    tracking_error: float = 0.0025  # ETF 跟踪误差预算
    impact: float = 0.0015        # 冲击成本 (用于无套利区间阈值)
    exec_slippage: float = 0.0003  # 实际成交滑点 (流动性宽基, 限价分批, 低于阈值口径)
    rf: float = 0.02              # 闲置保证金计息 (银河: 年化2%)
    funding: float = 0.06         # 资金成本 (银河: 年化6%)

    @property
    def round_trip(self) -> float:
        """No-arbitrage *band* width (signal gating, 华泰柏瑞 conservative)."""
        return 2 * (self.fut_fee + self.etf_fee + self.impact) + self.tracking_error

    @property
    def exec_one_way(self) -> float:
        """Realised one-way execution cost for P&L (both legs, liquid products)."""
        return self.fut_fee + self.etf_fee + self.exec_slippage


COSTS = Costs()


@dataclass(frozen=True)
class SignalParams:
    """Thresholds for the two strategy variants."""

    # 基差收敛 (银河/东证): 年化基差率阈值, 持有升水至收敛
    conv_enter_rate: float = 0.03       # 年化(分红调整)基差 >=3% 入场 (正向套利)
    conv_exit_rate: float = -0.005      # 持有整个升水regime, 跌破-0.5%才平 (低换手, hysteresis)

    # 银河式: 滚动历史分位数阈值 (annualised basis rate percentile)
    percentile_window: int = 250        # ~1 trading year rolling lookback
    enter_high_pct: float = 0.80        # >80分位 -> 正向套利 (期货升水)
    enter_low_pct: float = 0.20         # <20分位 -> 反向套利 (期货贴水)
    exit_pct: float = 0.50              # 回归中位数平仓

    # 东证式: 均值回归 z-score
    zscore_window: int = 60
    z_enter: float = 1.5
    z_exit: float = 0.3

    # 反向套利融券约束: 若 True, 跳过所有需要做空ETF的反向套利腿 (诚实地反映A股现实)
    allow_short_etf: bool = False


SIGNALS = SignalParams()


@dataclass(frozen=True)
class RegimeParams:
    """Basis-regime classifier parameters.

    Regime labels:
      +1  premium / contango: futures rich versus ETF/index spot
      -1  discount / backwardation: futures cheap versus ETF/index spot
       0  neutral: inside the no-trade band or not yet confirmed
    """

    enter_rate: float = 0.01      # annualised dividend-adjusted basis needed to confirm a side
    exit_rate: float = 0.002      # hysteresis band; do not flip until basis crosses back inside
    min_confirm_days: int = 3     # suppress one-day basis spikes around rolls/dividend dates
    use_dividend_adjusted: bool = True


REGIME = RegimeParams()


@dataclass(frozen=True)
class ExecutionParams:
    """Track A execution-simulation parameters.

    These are deliberately conservative defaults for a daily-data proxy of an
    hftbacktest queue/fill model. Real L2 snapshots can replace the synthetic
    book adapter without changing the runner output schema.
    """

    child_slices: int = 8
    tick_size: float = 0.2
    spread_ticks: int = 2
    passive_depth_bps: float = 3.0
    active_cross_bps: float = 1.5
    queue_decay: float = 0.35
    max_passive_wait_slices: int = 4
    target_notional: float = 10_000_000.0
    # Intraday time-of-day at which a recorded L2 session is sampled into the
    # daily execution book (last snapshot at/before this time). Synthetic-book
    # runs ignore it.
    snapshot_sample_time: str = "14:55:00"


EXECUTION = ExecutionParams()

# Published benchmark numbers for side-by-side validation in the report.
# 东证期货 (基差收敛, 动态ETF, 2018+):  年化收益 / 平均最大回撤 / 持仓周期≈40天
REPORT_CONVERGENCE = {
    "IH+50ETF":   {"ann_return": 0.038, "max_dd": -0.004},
    "IF+300ETF":  {"ann_return": 0.058, "max_dd": -0.006},
    "IC+500ETF":  {"ann_return": 0.047, "max_dd": -0.005},
    "IM+1000ETF": {"ann_return": 0.019, "max_dd": -0.006},
}
# 东证期货 (均值回归, 固定ETF, 2023+ 费后年化)
REPORT_MEANREV = {
    "IH+50ETF": 0.048, "IF+300ETF": 0.092, "IC+500ETF": 0.105, "IM+1000ETF": 0.121,
}
# 银河期货 多品种复合 (2016-2024.4): 年化6.57% / 最大回撤3.37% / 夏普1.78
REPORT_GALAXY_COMPOSITE = {"ann_return": 0.0657, "max_dd": -0.0337, "sharpe": 1.78}

TRADING_DAYS = 244  # CFFEX/A股 年交易日近似

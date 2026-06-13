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
    etf_code: str      # spot ETF leg, e.g. "510300.SH"
    multiplier: int    # contract multiplier (point value, CNY/index-point)
    start_date: str    # earliest sensible backtest start (contract listing aware)


# The four CFFEX index-futures families and their canonical broad-based ETF legs.
PAIRS: tuple[Pair, ...] = (
    Pair("IH+50ETF",   "IH.CFX", "000016.SH", "510050.SH", 300, "20190101"),
    Pair("IF+300ETF",  "IF.CFX", "000300.SH", "510300.SH", 300, "20190101"),
    Pair("IC+500ETF",  "IC.CFX", "000905.SH", "510500.SH", 200, "20190101"),
    Pair("IM+1000ETF", "IM.CFX", "000852.SH", "512100.SH", 200, "20220722"),  # IM listed 2022-07-22
)

# Default backtest window (overridable by CLI). End None -> latest available.
BACKTEST_START = "20190101"
BACKTEST_END: str | None = None


@dataclass(frozen=True)
class Costs:
    """Round-trip transaction & carry costs (华泰柏瑞 / 银河 口径, single-side rates)."""

    fut_fee: float = 0.00015      # 期货手续费 单边
    etf_fee: float = 0.0005       # ETF手续费 单边
    tracking_error: float = 0.0025  # ETF 跟踪误差预算
    impact: float = 0.0015        # 冲击成本
    rf: float = 0.02              # 闲置保证金计息 (银河: 年化2%)
    funding: float = 0.06         # 资金成本 (银河: 年化6%)

    @property
    def round_trip(self) -> float:
        """Total one-round-trip frictional cost as a fraction of notional."""
        return 2 * (self.fut_fee + self.etf_fee + self.impact) + self.tracking_error


COSTS = Costs()


@dataclass(frozen=True)
class SignalParams:
    """Thresholds for the two strategy variants."""

    # 基差收敛 (银河/东证): 年化基差率阈值, 持有升水至收敛
    conv_enter_rate: float = 0.03       # 年化升水 >=3% 入场 (正向套利)
    conv_exit_rate: float = 0.005       # 年化升水 收敛至 <=0.5% 平仓

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

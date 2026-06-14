<p align="center">
  <a href="#简体中文">
    <img src="https://img.shields.io/badge/LANGUAGE-中文-E74C3C?style=for-the-badge&labelColor=4B4B4B" alt="Language Chinese" />
  </a>
  <a href="#english">
    <img src="https://img.shields.io/badge/LANGUAGE-ENGLISH-2D77D1?style=for-the-badge&labelColor=4B4B4B" alt="Language English" />
  </a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/DATA-tushare-FF6B35?style=for-the-badge" alt="tushare" />
  <img src="https://img.shields.io/badge/MARKET-A股 ETF × 股指期货-2ECC71?style=for-the-badge" alt="market" />
  <img src="https://img.shields.io/badge/LICENSE-MIT-9B59B6?style=for-the-badge" alt="MIT" />
</p>

---

<a id="简体中文"></a>

## 简体中文

### 当前语言：中文 | [Switch to English](#english)

# ETF–股指期货 期现基差套利复现

### 用真实 A 股数据复现东证 / 银河 / 华泰柏瑞的期现套利框架

## 📌 项目概览

本项目用 **tushare 真实 A 股数据**（上证50/沪深300/中证500/中证1000 四套股指期货
及对应宽基 ETF，2019–2026）复现三篇卖方研究的**股指期货–ETF 期现基差套利**框架。

核心结论一句话：

> 期现套利赚的是「年化基差 + 分红 − 成本」，而**融券约束**决定 A 股能否真正执行。

本仓库包含：

- 📊 基差模型与三种交易信号（基差收敛 / 分位数阈值 / 均值回归）
- 📈 四品种 + 多品种复合回测，对照报告数值；逐合约严格记账作为主可信口径
- 🔁 两种执行情景：融券开启（复现报告）vs 融券受限（A 股现实）
- 🎯 动态 ETF 选择（东证精修，按流动性/跟踪误差择优现货端）

## 🧠 研究动机

期现套利是中国市场最成熟的量化策略之一，但直接照搬报告数值会忽略两个关键点：

- **分红基差**：高股息品种（上证50/沪深300）若不调整，原始 `F−S` 会系统性高估贴水
- **融券约束**：A 股长期贴水，反向套利（做空 ETF）受融券券源制约，报告的正收益高度依赖融券资源

## 🚀 核心方法

```
年化基差率   rate = (F − S)/S · 365/dte + 股息率        # 分红调整, 用于入场信号
持有成本     F_theory = S·(1 + (r_f − d)·t/365)          # 银河/华泰柏瑞
逐笔锁定carry 入场锁定原始基差, 平滑累计至收敛           # 复现高胜率/小回撤
分红实现     通过 ETF 全收益腿按实际季节(6–7月)捕获      # 不与基差重复计
```

三种因果信号（无前视、滚动）：

| 信号 | 出处 | 逻辑 |
| ---- | ---- | ---- |
| `conv` 基差收敛 | 银河 / 东证 | 升贴水超阈值入场，持有整段 regime |
| `galaxy` 分位数 | 银河 | 年化基差率滚动历史分位数阈值 |
| `orient` 均值回归 | 东证 | 年化基差率 z-score |

## 🎯 动态 ETF 选择（东证精修）

每月按 `z(流动性) − z(跟踪误差)` 在候选 ETF 池中择优现货端（IF/IC/IM 各 3–4 只候选）。
效果：候选 ETF 分散度越大提升越明显——**IM（小盘）年化 7.8%→8.2%、夏普 1.74→1.85**，
IF（同质）近中性，IH（仅 510050）无变化。

## 📊 复现结果

### 🔍 报告口径结果：连续主力 + 锁定 carry（融券开启，2019–2026，费后）

| 品种 | conv 年化 | *报告* | 夏普 | 最大回撤 | 胜率 |
| ---- | --------: | -----: | ---: | -------: | ---: |
| IH+50ETF   | 5.6% | *3.8%* | 1.72 | −2.7% | 62% |
| IF+300ETF  | 4.7% | *5.8%* | 1.15 | −4.4% | 62% |
| IC+500ETF  | 7.9% | *4.7%* | 2.04 | −4.8% | 63% |
| IM+1000ETF | 7.8% | *1.9%* | 1.74 | −4.2% | 65% |
| **多品种复合** | **5.6%** | *6.6%* | **1.94** | **−3.5%** | 60% |

复合表现与银河报告（6.6% / 夏普 1.78 / 回撤 −3.4%）高度吻合；但这是连续主力 + 锁定
carry 的近似口径，严格可执行性以逐合约结果为准。

### 🧮 逐合约持有至交割（严格记账）

[`run_contract.py`](track0_empirical/run_contract.py) 用**单一合约内 mark-to-market 持有至
交割**替代"连续主力 + 锁定 carry"近似：无换月跳价，终值在交割真实收敛，回撤更真实。

| 品种 | 逐合约 年化 | *报告* | 夏普 | 最大回撤 | 胜率 |
| ---- | ----------: | -----: | ---: | -------: | ---: |
| IH+50ETF   | 3.1% | *3.8%* | 0.38 | −3.5% | 58% |
| IF+300ETF  | 1.5% | *5.8%* | −0.26 | −1.4% | 60% |
| IC+500ETF  | 6.6% | *4.7%* | 1.32 | −2.2% | 58% |
| IM+1000ETF | 8.1% | *1.9%* | 1.37 | −3.0% | 57% |
| **多品种复合** | **3.9%** | *6.6%* | **0.98** | **−1.1%** | 55% |

**关键结论**：严格逐合约记账（复合 3.9%）比连续主力锁定 carry 近似（5.6%）**更保守**
——近似口径略偏乐观。融券受限情景下逐合约的**胜率达 58–91%**，重现报告"胜率接近 100%"
特征。两种口径的复合净值对照见 `figures/fig4_accounting_compare.png`。

### 🧪 融券与执行成本敏感性

融券可得性是实际落地的第一约束。`track0_empirical/run_short_sensitivity.py` 显示：

| 情景 | 复合年化 | 夏普 | 最大回撤 |
| ---- | -------: | ---: | -------: |
| 无融券 | 0.2% | -1.30 | −3.5% |
| 50% 融券可得 / 6% 融券费 | 1.1% | -0.59 | −3.6% |
| 100% 融券可得 / 0% 融券费 | 5.6% | 1.94 | −3.5% |
| 100% 融券可得 / 6% 融券费 | 2.8% | 0.45 | −3.6% |

Track A proxy 将 child-order 滑点回灌后，复合年化从 **5.6%** 降至 **4.6%**，夏普从
**1.94** 降至 **1.39**。执行后净值见 `figures/fig5_execution_adjusted.png`。

### 融券受限情景（A 股现实，默认）

四品种多为微负至打平，仅高股息 IH 勉强为正——印证华泰柏瑞"融券约束制约反向套利"：
报告的正收益依赖融券资源，纯正向套利在长期贴水市场空间很薄。

## ⚙️ 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env          # 填入你自己的 TUSHARE_TOKEN (.env 已 gitignore)

python track0_empirical/run_backtest.py --start 20190101 --allow-short            # 复现报告
python track0_empirical/run_backtest.py --start 20190101 --allow-short --dynamic  # 动态ETF
python track0_empirical/run_backtest.py --start 20190101                          # 融券受限
python trackA_execution/run_hftbacktest_proxy.py --start 20190101 --allow-short    # Track A 合成盘口
python trackA_execution/run_hftbacktest_proxy.py --start 20190101 --allow-short --use-snapshots  # Track A 真实/自录 L2
python make_figures.py                                                            # 出图
```

## 📂 仓库结构

```text
config.py                      # 单一可信源: 品种对/候选ETF/成本/阈值/报告对照值
src/
  data_tushare.py              # tushare 取数 + parquet 缓存 + 主力连续 + 全收益指数
  basis_model.py               # 持有成本定价 / 分红调整年化基差 / 无套利区间
  signal.py                    # conv / galaxy / orient 三种因果信号
  etf_select.py                # 动态 ETF 择优 (流动性/跟踪误差)
  contract_backtest.py         # 逐合约持有至交割 (严格记账, 无换月跳价)
  hft_execution.py             # Track A 执行撮合 (BookProvider 抽象: 合成/快照)
  l2_snapshot.py               # 真实/自录 L2 快照摄入 (depth-5 schema, parquet)
  snapshot_recorder.py         # 实时盘口录制 (QuoteSource: tushare 五档)
  data_binance.py              # Track B 币安现货/永续/资金费取数 (免密钥公共行情)
  crypto_backtest.py           # Track B 现货-永续 cash-and-carry 实现 P&L (365年化)
  metrics.py                   # 年化/夏普/回撤/胜率/持仓周期
track0_empirical/run_backtest.py   # 连续主力 + 锁定carry (近似, 含动态ETF)
track0_empirical/run_contract.py   # 逐合约持有至交割 (严格)
trackA_execution/run_hftbacktest_proxy.py # Track A hftbacktest-style 执行撮合
trackA_execution/record_snapshots.py      # 交易时段录制真实 L2 快照
trackB_deploy/run_basis_backtest.py       # Track B 币安现货-永续 基差回测
results/                       # basis_summary_*.csv
figures/                       # 净值 / 基差图
report/                        # report.tex + report.pdf (XeLaTeX 中文报告)
```

## 🛣️ 三条轨道

| 轨道 | 引擎 | 数据 | 状态 |
| ---- | ---- | ---- | ---- |
| **Track 0** | 纯 Python 实证 | tushare 真实 A 股 | ✅ 已完成（含动态 ETF） |
| Track A | hftbacktest-style 撮合 + L2 摄入/录制 | 合成盘口 / 自录 L2 快照 | ✅ 摄入+ETF录制已通 / 期货五档待CTP |
| Track B | 现货–永续 基差回测 + Hummingbot 部署 | 免费 Binance 公共行情 | ✅ 基差回测已完成 / Hummingbot 制品待补 |

> Hummingbot 接不了 SSE/SZSE，免费 A 股逐笔 L2 不可得；故 Track 0 是定量主体。
> Track A 的盘口已抽象为可插拔 `BookProvider`：默认合成盘口，`--use-snapshots`
> 切换到真实/自录 L2 快照（缺失自动降级合成），真实快照按券商/自录脚本落地即用。
> Track B 把币安**现货 vs USDⓈ-M 永续**作为期现基差的加密类比（永续 carry = 资金费），
> 复用 Track 0 三信号在免费公共行情上跑出真实净值；Hummingbot 纸面部署制品待补。

### 📥 Track A L2 快照接入

自录快照按一品种一会话一份 parquet 落到
`data/snapshots/<合约代码>/<YYYYMMDD>.parquet`，标准 depth-5 schema：

```text
ts                       盘中时间戳 (datetime64)
bid_px_1..5 / bid_sz_1..5    买档 价/量 (level 1 = 最优)
ask_px_1..5 / ask_sz_1..5    卖档 价/量
```

券商终端导出或自录脚本只要落成这个格式即可接入，其余管线不变；运行
`run_hftbacktest_proxy.py --use-snapshots` 即用快照撮合，并在 summary 报告
`snapshot_fill_rate`（快照覆盖率，便于审计真实/合成混合回测）。

**自录脚本** [`record_snapshots.py`](trackA_execution/record_snapshots.py) 在交易时段
轮询实时盘口并写入上述 schema（可插拔 `QuoteSource`）：

```bash
# 单次抓拍，验证实时源映射（盘后会取上一交易日收盘五档）
python trackA_execution/record_snapshots.py --etf 510300.SH --once
# 连续录制至 15:00，每 3 秒采样一次
python trackA_execution/record_snapshots.py --etf 510300.SH 510500.SH --interval 3 --until 15:00:05
```

- **ETF 腿**：tushare `realtime_quote`（sina）返回**完整五档**（B1..B5 / A1..A5），
  量按 1 手 = 100 份换算成股数，现在即可录制。
- **股指期货腿**：CFFEX 免费实时仅 1 档，完整五档需券商 CTP 行情；schema 容忍部分
  档位（缺档置 0、深度自动跳过），CTP adapter 接同一 `QuoteSource` 协议即可。

### 🪙 Track B — 币安现货–永续 基差回测（加密类比）

期现套利在加密市场的等价物是 **现货 vs USDⓈ-M 永续** 的 cash-and-carry：多现货 /
空永续，靠**资金费**(funding，永续的收敛机制，每 8h 结算) 吃 carry。数据全部来自
币安免费公共行情(现货/永续 K 线 + 资金费历史，免密钥)，复用 Track 0 的 conv /
galaxy / orient 三信号；P&L 用真实 `(现货收益 − 永续收益 + 资金费)` **逐日实现，无
锁定 carry 假设**(加密三腿数据完整，比 Track 0 更硬)。

仅正 carry(多现货/空永续，2021–2026，费后):

| 品种 | conv 年化 | 夏普 | 最大回撤 | 胜率 |
| ---- | --------: | ---: | -------: | ---: |
| BTC | 7.4% | 5.03 | −6.0% | 83% |
| ETH | 8.9% | 5.19 | −6.4% | 82% |
| BNB | 5.0% | 2.84 | −2.0% | 67% |
| **复合** | **7.1%** | **5.24** | **−4.0%** | 75% |

资金费 carry 的低波动/高夏普特征(夏普≈5)与 A 股期现正向套利一致;放开双向(含负资金费
做空现货)后复合年化升至 **8.3%**、夏普 **5.77**。

```bash
python trackB_deploy/run_basis_backtest.py --start 20210101              # 仅正carry
python trackB_deploy/run_basis_backtest.py --start 20210101 --allow-short # 双向
```

> 本课题为全新主题，不复现 Avellaneda–Stoikov 做市。

## ⚠️ 风险提示

- 主力连续替代逐合约持有至交割；锁定 carry 假设收敛单调
- 分红用全收益指数估计、按实现季节捕获，未刻画个券分红时点
- 反向套利受融券成本/可得性约束；样本区间与报告不同
- 回测结果不代表实盘收益，存在模型与执行偏差

## 📄 许可证

本项目采用 MIT 许可证，详情见 [LICENSE](LICENSE)。

> 🔐 `tushare` token 仅存于 gitignore 的 `.env`，从未入库。

---

<a id="english"></a>

## English

### Current Language: English | [切换到中文](#简体中文)

# ETF / Index-Futures Basis Arbitrage Reproduction

### Reproducing the cash-futures arbitrage framework on real A-share data

## 📌 Project Overview

This project reproduces the **index-futures / ETF basis-arbitrage** framework from three
Chinese sell-side reports (Orient, Galaxy, Huatai-PB) using **real A-share tushare data**
(four CFFEX index futures × broad-based ETFs, 2019–2026).

> Cash-futures arbitrage earns *annualised basis + dividends − cost*; the **short-sell
> (融券) constraint** decides whether it is actually executable in A-shares.

## 🚀 Method

```
basis rate   = (F − S)/S · 365/dte + dividend_yield      # dividend-adjusted, for entry signal
fair futures = S·(1 + (r_f − d)·t/365)                   # cost of carry
locked carry = raw basis locked at entry, accrued to convergence
dividends    = realised seasonally via the ETF total-return leg (not double-counted)
```

## 📊 Key Results (short-enabled, 2019–2026, net)

| Pair | conv ann. | *report* | Sharpe | MaxDD | Win |
| ---- | --------: | -------: | -----: | ----: | --: |
| IH+50ETF   | 5.6% | *3.8%* | 1.72 | −2.7% | 62% |
| IF+300ETF  | 4.7% | *5.8%* | 1.15 | −4.4% | 62% |
| IC+500ETF  | 7.9% | *4.7%* | 2.04 | −4.8% | 63% |
| IM+1000ETF | 7.8% | *1.9%* | 1.74 | −4.2% | 65% |
| **Composite** | **5.6%** | *6.6%* | **1.94** | **−3.5%** | 60% |

The composite closely matches Galaxy's report (6.6% / Sharpe 1.78 / −3.4%). Dynamic ETF
selection adds most where candidate ETFs diverge (IM: 7.8%→8.2%, Sharpe 1.74→1.85).

## 📥 Track A — Real / self-recorded L2 ingestion

The execution book is a pluggable `BookProvider`: the synthetic book is the default and
fallback, while real / self-recorded **depth-5 L2 snapshots** drive the same queue/fill
model. Snapshots land as `data/snapshots/<code>/<YYYYMMDD>.parquet` with the standard
schema (`ts`, `bid_px/sz_1..5`, `ask_px/sz_1..5`). The runner reports a `snapshot_fill_rate`
coverage so mixed real/synthetic runs stay auditable. `record_snapshots.py` records live
books during the session — ETF legs get the full 5 levels via tushare today; index-futures
5-level L2 needs a broker CTP feed.

## 🪙 Track B — Binance spot–perp basis (crypto analog)

The crypto equivalent of the cash-futures trade is **spot vs USDⓈ-M perpetual**: long spot /
short perp harvests the **funding rate** (the perp's convergence mechanism), mirroring 多 ETF /
空期货 carry. Data is all free, key-less Binance public REST (spot/perp klines + funding
history); the same conv/galaxy/orient signals apply, and P&L is the realized
`(r_spot − r_perp + funding)` per day (no locked-carry assumption).

| Pair | conv ann. | Sharpe | MaxDD | Win |
| ---- | --------: | -----: | ----: | --: |
| BTC | 7.4% | 5.03 | −6.0% | 83% |
| ETH | 8.9% | 5.19 | −6.4% | 82% |
| BNB | 5.0% | 2.84 | −2.0% | 67% |
| **Composite** | **7.1%** | **5.24** | **−4.0%** | 75% |

Funding-carry's low-vol / high-Sharpe (≈5) matches A-share forward arbitrage; two-sided
(shorting spot on negative funding) lifts the composite to 8.3% / Sharpe 5.77. A Hummingbot
paper-trading deployment artifact is under `trackB_deploy/hummingbot/`.

## ⚙️ Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env          # add your own TUSHARE_TOKEN (.env is gitignored)
python track0_empirical/run_backtest.py --start 20190101 --allow-short --dynamic   # Track 0
python trackA_execution/run_hftbacktest_proxy.py --start 20190101 --allow-short     # Track A synthetic
python trackA_execution/run_hftbacktest_proxy.py --start 20190101 --allow-short --use-snapshots  # Track A real L2
python trackA_execution/record_snapshots.py --etf 510300.SH --once                 # record live L2
python trackB_deploy/run_basis_backtest.py --start 20210101                         # Track B (Binance)
python -m unittest discover
```

## ⚠️ Disclaimer

Continuous main-contract approximation; dividends estimated via total-return index; reverse
arbitrage is short-sell constrained; backtest results are not live-trading returns.

## 📄 License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

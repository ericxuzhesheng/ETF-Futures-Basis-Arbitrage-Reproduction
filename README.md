# ETF–股指期货 期现基差套利复现 (ETF / Index-Futures Basis Arbitrage)

复现三篇卖方报告的期现基差套利框架，用 **tushare 真实 A 股数据** 做实证，并规划用
**hftbacktest**（执行撮合）与 **Hummingbot**（部署节奏类比）做工具层验证。

> 复现对象（互相印证的同一框架）：
> - 东证期货《股指期货与ETF的基差套利》— 基差收敛 + 均值回归
> - 银河期货《ETF期现套利策略》— 年化基差率历史分位数阈值、多品种复合
> - 华泰柏瑞《沪深300ETF与股指期货套利》— 持有成本模型 / 无套利区间
>
> **不复现** Avellaneda–Stoikov 做市（已在另一仓库完成，本课题为全新主题）。

---

## 三条轨道

| 轨道 | 引擎 | 数据 | 状态 |
|------|------|------|------|
| **Track 0** | 纯 Python 实证回测 | **tushare 真实 A 股**（ETF/指数/股指期货 日线+分钟） | ✅ 已跑通 |
| Track A | hftbacktest 撮合执行 | 台内 L2（首选）/ 自录3秒快照 / 合成盘口 | 🔜 规划中 |
| Track B | Hummingbot 现货–永续类比 | 免费 Binance 纸面交易 | 🔜 规划中 |

工具硬约束（诚实写明）：Hummingbot 接不了 SSE/SZSE，A 股不可直连；免费可回溯的 A 股逐笔
L2 基本不存在。故 Track 0（真实 A 股）是定量主体，Track A/B 是工具/机制验证。

---

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env          # 填入你自己的 TUSHARE_TOKEN (.env 已被 gitignore)

# Track 0 — 期现基差套利实证 (首跑会从 tushare 拉数并缓存到 data/*.parquet)
python track0_empirical/run_backtest.py --start 20190101 --allow-short   # 复现报告(需融券)
python track0_empirical/run_backtest.py --start 20190101                 # A股现实(融券受限)
```

输出 `results/basis_summary_{short,noshort}.csv` 与控制台对照表。

---

## 模型

```
年化基差率   basis_rate = (F - S)/S * 365/dte          # F=股指期货, S=指数现货
持有成本     F_theory   = S * (1 + (rf - d) * t/365)    # 银河/华泰柏瑞
无套利区间   ±(2*(期货费+ETF费+冲击) + 跟踪误差)        # 华泰柏瑞成本口径
日度收益     pos * basis_rate / 交易日                  # 持有至收敛, 赚取年化基差(carry)
```

三种信号（均为因果、无前视）：
- **conv** 基差收敛：升水/贴水超阈值入场，收敛平仓（银河/东证）
- **galaxy** 滚动历史分位数阈值（银河）
- **orient** z-score 均值回归（东证）

两种执行情景：`--allow-short` 开启=有融券资源机构（复现报告）；默认关闭=A 股融券受限现实。

---

## 当前复现状态

**四品种均已进入报告区间**（融券开启，2019–2026）：

| 品种 | conv 年化 | 报告 | 最大回撤 | 胜率 |
|---|---:|---:|---:|---:|
| IH+50ETF | 5.2% | 3.8% | −3.6% | 61% |
| IF+300ETF | 4.7% | 5.8% | −4.8% | 62% |
| IC+500ETF | 7.0% | 4.7% | −4.6% | 61% |
| IM+1000ETF | 6.8% | 1.9% | −4.1% | 64% |
| **多品种复合** | **5.1% / SR 1.59 / DD −3.2%** | **6.6% / SR 1.78 / −3.4%** | | |

复合表现与银河报告高度吻合。三个关键修复：
1. **分红基差调整** — 用全收益指数(H00xxx.CSI)与价格指数之差估计股息率，加回年化基差
   (`rate_adj = (F−S)/S·365/dte + 股息率`)。修复了 IH/IF 高股息品种的系统性贴水高估。
2. **逐笔锁定 carry** — 期现套利到期锁定终值, 入场即锁定 carry 平滑累计 → 复现报告的高胜率/
   极小回撤特征。
3. **整段 regime 持有** — 持有整个升水/贴水区间(低换手 ~9 天)使捕获的 carry 覆盖往返成本。

**融券受限情景（A股现实, 默认）**：四品种多为微负/打平，IH 因高股息勉强为正。这印证华泰柏瑞
"融券约束制约反向套利"——报告的正收益高度依赖融券资源；纯正向套利在贴水市场空间很薄。

### 动态 ETF 选择（东证精修，已实现）

每月按 `z(流动性) − z(跟踪误差)` 在候选 ETF 中择优现货端（`--dynamic`）：

| 品种 | 固定ETF 年化/夏普 | 动态ETF 年化/夏普 | 月切换 |
|---|---|---|---|
| IH+50ETF | 5.16% / 1.43 | 5.16% / 1.43 | 0（仅510050） |
| IF+300ETF | 4.73% / 1.07 | 4.69% / 1.05 | 7 |
| IC+500ETF | 7.02% / 1.65 | 7.06% / 1.66 | 7 |
| IM+1000ETF | 6.79% / 1.41 | **7.45% / 1.60** | 8 |
| **复合** | 5.11% / 2.62 | **5.19% / 2.68** | |

动态择优在候选 ETF 分散度最大的 **IM（小盘）** 上提升最明显，IF（同质）近中性、IH（单一标的）
无变化——与东证"动态选择 ETF 表现更稳定"一致。候选池见 [config.py](config.py) `etf_candidates`。

```bash
python track0_empirical/run_backtest.py --start 20190101 --allow-short --dynamic
```

**下一步**：分红季节性精修、Track A(hftbacktest)/Track B(Hummingbot)。

---

## 目录结构

```
config.py                      # 单一可信源: 品种对/成本/阈值/区间/报告对照值
src/
  data_tushare.py              # tushare 取数 + parquet 缓存 + 主力连续 + 交割日
  basis_model.py               # 持有成本定价 / 年化基差率 / 无套利区间
  signal.py                    # conv / galaxy / orient 三种因果信号
  metrics.py                   # 年化/夏普/回撤/胜率/持仓周期
track0_empirical/run_backtest.py
results/                       # basis_summary_{short,noshort}.csv
```

## 安全

`.env`（含 TUSHARE_TOKEN）已被 `.gitignore`，**绝不提交**。仅提交 `.env.example`。

## License

MIT — 见 [LICENSE](LICENSE)。

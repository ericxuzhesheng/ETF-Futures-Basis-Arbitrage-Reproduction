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

## 当前复现状态（诚实）

**已成立**：完整数据管道（4 品种真实 tushare 数据）、基差/年化基差率计算、3 种信号、两种
融券情景、指标体系。**IC/IM 基差收敛已进入报告区间**（融券开启：IC +2.5% / IM +3.3%，
对照东证 4.7% / 1.9%；胜率 57–64%）。

**未对齐 / 下一步校准**（这正是周中要过的研究迭代）：
1. **分红基差未处理** — IH/IF 为高股息大盘，原始 `F-S` 系统性高估贴水，导致信号偏差与
   回撤偏大；IC/IM（低股息）因偏差小而接近报告。**首要修复**：用指数分红点调整基差
   （报告明确要求"分红基差要处理"）。
2. 持有至交割的逐合约 PnL 记账（替代连续主力近似），更贴近报告 ~40 天持仓与近 100% 胜率。
3. 动态 ETF 选择（东证）与 ETF 折溢价叠加（固定 ETF）。

> 结论方向正确、机制成立、IC/IM 已对齐；高股息品种的 bp 级对齐依赖分红基差调整，列为下一步。

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

# Track B — Hummingbot 现货–永续 基差套利 部署制品

把 Track B 的 cash-and-carry 信号（多现货 / 空永续吃资金费）部署成 Hummingbot
**纸面交易**策略,作为 ETF–股指期货期现套利的加密类比的「部署节奏」验证层。

> ⚠️ 这是部署制品,结构按 Hummingbot 脚本策略约定编写。**先在 paper / testnet 跑通**
> 再谈实盘。免费 Binance 纸面交易无需资金,适合验证机制与挂机节奏。

## 文件

```text
docker-compose.yml                       # Hummingbot 容器 (挂载下列目录)
scripts/spot_perp_basis_arb.py           # 脚本策略: 资金费 carry 收敛 (conv 信号)
conf/conf_spot_perp_basis_arb.yml        # 参数 (连接器/标的/阈值, 对齐回测)
logs/  data/                             # 运行时生成 (gitignore)
```

## 策略逻辑（与回测一致）

| 信号 | 动作 |
| ---- | ---- |
| 年化资金费 + premium ≥ `entry_rate`(默认 3%) | 开 carry: 买现货 + 卖永续(等额) |
| 衰减至 ≤ `exit_rate`(默认 −0.5%) | 平双腿 |

阈值与 [`config.py`](../../config.py) 的 `SignalParams.conv_enter_rate/conv_exit_rate`
一致,故实盘行为跟踪 [`run_basis_backtest.py`](../run_basis_backtest.py) 的 `conv` 口径。

## 运行手册

```bash
# 1) 起容器 (本机已装 Docker)
cd trackB_deploy/hummingbot
HB_PASSWORD=yourpass docker compose up -d

# 2) 进客户端
docker attach hummingbot
#    首次会让你设置密码; 之后配置 paper / testnet 连接器:
#    >>> connect binance_paper_trade          (纸面现货, 无需 API key)
#    >>> connect binance_perpetual_testnet    (永续 testnet, 填 testnet API key)

# 3) 启动策略
#    >>> start --script spot_perp_basis_arb.py --conf conf_spot_perp_basis_arb.yml
#    >>> status        # 查看当前 carry / 持仓状态
#    Ctrl-P Ctrl-Q 脱离容器 (策略继续挂机)
```

## 与定量主体的关系

- **定量主体**:[`run_basis_backtest.py`](../run_basis_backtest.py) 用真实币安历史数据
  跑出净值(conv 复合 ~7% 年化 / 夏普 ~5)。
- **本部署制品**:把同一信号搬到 Hummingbot 实时撮合,验证开/平腿、资金费结算、
  挂机节奏。两者阈值同源,便于回测—实盘对照。

> ⚠️ **API 版本兼容**:脚本按 Hummingbot 脚本策略接口编写,但未固定到具体版本。
> `get_funding_info().rate`、`PositionAction`、`buy/sell(..., position_action=...)` 等在不同
> Hummingbot 版本可能有差异;若 `start` 时报 AttributeError/TypeError,按所装版本微调。

> 真实实盘需自担风险与合规审查;永续合约有强平风险,务必先 testnet 验证保证金与强平逻辑。

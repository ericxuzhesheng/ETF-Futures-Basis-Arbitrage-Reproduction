"""Hummingbot script strategy — spot/perpetual basis (funding) cash-and-carry.

Track B deployment artifact. This is the *crypto analog* of the A-share
ETF / index-futures basis arbitrage reproduced in Track 0: long spot + short
perpetual harvests the funding rate (the perpetual's convergence mechanism),
mirroring 多 ETF / 空期货 收敛 carry.

It deploys the same `conv` (basis-convergence) logic Track B backtests in
`trackB_deploy/run_basis_backtest.py`, but as a live/paper Hummingbot strategy:

    enter +carry  when annualised funding (+ premium) >= entry_rate
                  -> BUY spot, SELL perp (notional-neutral)
    exit          when it decays below exit_rate -> flatten both legs

Run on PAPER first (binance_paper_trade + binance_perpetual_testnet). See the
sibling README.md for the full run manual. Thresholds match config.SignalParams
(conv_enter_rate=0.03, conv_exit_rate=-0.005) so live behaviour tracks the
backtest.

API COMPATIBILITY: this targets the Hummingbot script-strategy interface but has
not been pinned to a specific release. Verify against your installed version —
notably `connector.get_funding_info(pair).rate`, `PositionAction`, and the
`buy/sell(..., position_action=...)` signature can differ across Hummingbot
versions. Adjust if the client raises AttributeError / TypeError on start.
"""

from decimal import Decimal

from hummingbot.core.data_type.common import OrderType, PositionAction, PositionSide, TradeType
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase


class SpotPerpBasisArb(ScriptStrategyBase):
    # ---- configuration (override via conf/conf_spot_perp_basis_arb.yml) -------
    spot_connector = "binance_paper_trade"
    perp_connector = "binance_perpetual_testnet"
    trading_pair = "BTC-USDT"

    order_amount_usd = Decimal("500")     # notional per leg
    funding_per_year = Decimal("365")     # annualise the *daily* funding sum
    entry_rate = Decimal("0.03")          # ann. funding(+premium) to open  (conv_enter_rate)
    exit_rate = Decimal("-0.005")         # decay level to flatten           (conv_exit_rate)
    min_spread_to_act = Decimal("0.0002")  # skip if legs would cross at a loss
    tick_throttle_s = 15                   # act at most this often

    markets = {
        spot_connector: {trading_pair},
        perp_connector: {trading_pair},
    }

    def __init__(self, connectors):
        super().__init__(connectors)
        self._state = 0            # 0 flat, +1 long-spot/short-perp
        self._last_action_ts = 0.0

    # --------------------------------------------------------------------- #
    # signal
    # --------------------------------------------------------------------- #
    def annualised_carry(self) -> Decimal | None:
        """Annualised funding + spot-perp premium — the `basis_rate` analog."""
        perp = self.connectors[self.perp_connector]
        try:
            funding = perp.get_funding_info(self.trading_pair)
            funding_rate = Decimal(str(funding.rate))            # per 8h interval
        except Exception:
            return None
        spot_mid = self.spot_mid()
        perp_mid = self.perp_mid()
        if spot_mid is None or perp_mid is None or spot_mid == 0:
            return None
        # 3 funding settlements/day -> daily sum = rate*3; annualise by 365.
        funding_ann = funding_rate * Decimal("3") * self.funding_per_year
        premium = (perp_mid - spot_mid) / spot_mid
        return funding_ann + premium

    def spot_mid(self) -> Decimal | None:
        return self._mid(self.spot_connector)

    def perp_mid(self) -> Decimal | None:
        return self._mid(self.perp_connector)

    def _mid(self, connector: str) -> Decimal | None:
        c = self.connectors[connector]
        bid = c.get_price(self.trading_pair, False)
        ask = c.get_price(self.trading_pair, True)
        if bid is None or ask is None:
            return None
        return (bid + ask) / Decimal("2")

    # --------------------------------------------------------------------- #
    # main loop
    # --------------------------------------------------------------------- #
    def on_tick(self):
        now = self.current_timestamp
        if now - self._last_action_ts < self.tick_throttle_s:
            return
        carry = self.annualised_carry()
        if carry is None:
            return

        if self._state == 0 and carry >= self.entry_rate:
            self._open_carry(carry)
        elif self._state == 1 and carry <= self.exit_rate:
            self._flatten(carry)

    def _base_amount(self) -> Decimal:
        spot_mid = self.spot_mid()
        if spot_mid is None or spot_mid == 0:
            return Decimal("0")
        return self.order_amount_usd / spot_mid

    def _open_carry(self, carry: Decimal):
        amount = self._base_amount()
        if amount <= 0:
            return
        self.logger().info(f"OPEN carry: ann={carry:.2%} BUY spot + SELL perp {amount} {self.trading_pair}")
        self.buy(self.spot_connector, self.trading_pair, amount,
                 OrderType.MARKET, self.spot_mid())
        self.sell(self.perp_connector, self.trading_pair, amount,
                  OrderType.MARKET, self.perp_mid(),
                  position_action=PositionAction.OPEN)
        self._state = 1
        self._last_action_ts = self.current_timestamp

    def _flatten(self, carry: Decimal):
        amount = self._base_amount()
        if amount <= 0:
            return
        self.logger().info(f"FLATTEN: ann={carry:.2%} SELL spot + BUY perp {amount} {self.trading_pair}")
        self.sell(self.spot_connector, self.trading_pair, amount,
                  OrderType.MARKET, self.spot_mid())
        self.buy(self.perp_connector, self.trading_pair, amount,
                 OrderType.MARKET, self.perp_mid(),
                 position_action=PositionAction.CLOSE)
        self._state = 0
        self._last_action_ts = self.current_timestamp

    def format_status(self) -> str:
        if not self.ready_to_trade:
            return "Market connectors are not ready."
        carry = self.annualised_carry()
        lines = [
            f"  pair: {self.trading_pair}   state: {'LONG-CARRY' if self._state else 'FLAT'}",
            f"  annualised carry: {carry:.2%}" if carry is not None else "  carry: n/a",
            f"  entry>={self.entry_rate:.2%}  exit<={self.exit_rate:.2%}  notional/leg=${self.order_amount_usd}",
        ]
        return "\n".join(lines)

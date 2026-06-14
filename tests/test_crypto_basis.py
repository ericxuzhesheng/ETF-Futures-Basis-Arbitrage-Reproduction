import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import CryptoCosts
from src import crypto_backtest
from src.data_binance import FUNDING_PER_YEAR, build_basis_frame


class BuildBasisFrameTest(unittest.TestCase):
    def test_basis_rate_is_annualised_funding_plus_premium(self):
        dates = pd.to_datetime(["2024-01-01", "2024-01-02"])
        spot = pd.DataFrame({"date": dates, "close": [100.0, 100.0]})
        perp = pd.DataFrame({"date": dates, "close": [101.0, 100.0]})
        funding = pd.DataFrame({"date": dates, "funding_daily": [0.0003, 0.0001]})

        df = build_basis_frame(spot, perp, funding)

        self.assertListEqual(list(df["trade_date"]), list(dates))
        # day 1: premium = (101-100)/100 = 0.01, funding_ann = 0.0003*365
        self.assertAlmostEqual(df["premium"].iloc[0], 0.01)
        self.assertAlmostEqual(df["basis_rate_raw"].iloc[0], 0.0003 * FUNDING_PER_YEAR)
        self.assertAlmostEqual(df["basis_rate"].iloc[0], 0.0003 * FUNDING_PER_YEAR + 0.01)

    def test_missing_funding_filled_zero(self):
        dates = pd.to_datetime(["2024-01-01"])
        spot = pd.DataFrame({"date": dates, "close": [100.0]})
        perp = pd.DataFrame({"date": dates, "close": [100.0]})
        funding = pd.DataFrame({"date": pd.to_datetime([]), "funding_daily": []})
        df = build_basis_frame(spot, perp, funding)
        self.assertEqual(df["funding_daily"].iloc[0], 0.0)


class SimulateTest(unittest.TestCase):
    def _frame(self):
        dates = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"])
        return pd.DataFrame({
            "spot": [100.0, 110.0, 110.0],
            "perp": [100.0, 110.0, 110.0],
            "funding_daily": [0.001, 0.001, 0.001],
        }, index=dates)

    def test_long_carry_earns_funding_net_of_no_change_cost(self):
        df = self._frame()
        # hold +1 across all days; entry on day1 costs one exec_one_way
        pos = pd.Series([1, 1, 1], index=df.index)
        costs = CryptoCosts(spot_fee=0.0, perp_fee=0.0, slippage=0.0, rf=0.0)
        net = crypto_backtest.simulate(df, pos, costs)
        # day1: pos_lag=0 -> 0 ; day2: pos_lag=1, spot==perp move cancels, +funding 0.001
        self.assertAlmostEqual(net.iloc[0], 0.0)
        self.assertAlmostEqual(net.iloc[1], 0.001)
        self.assertAlmostEqual(net.iloc[2], 0.001)

    def test_cost_charged_on_position_change(self):
        df = self._frame()
        pos = pd.Series([1, 0, 0], index=df.index)  # open day1, close day2
        costs = CryptoCosts(spot_fee=0.001, perp_fee=0.0, slippage=0.0, rf=0.0)
        net = crypto_backtest.simulate(df, pos, costs)
        # day1 open: dpos=1 -> -0.001 ; day2 close: dpos=1 -> -0.001 (pos_lag=1 funding +0.001)
        self.assertAlmostEqual(net.iloc[0], -0.001)
        self.assertAlmostEqual(net.iloc[1], 0.001 - 0.001)

    def test_summarize_uses_365_annualisation(self):
        df = self._frame()
        pos = pd.Series([1, 1, 1], index=df.index)
        costs = CryptoCosts(spot_fee=0.0, perp_fee=0.0, slippage=0.0, rf=0.0)
        net = crypto_backtest.simulate(df, pos, costs)
        m = crypto_backtest.summarize(net, pos.shift(1).fillna(0))
        self.assertIn("ann_return", m)
        self.assertIn("sharpe", m)
        self.assertGreaterEqual(m["exposure"], 0.0)


if __name__ == "__main__":
    unittest.main()

import math
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import TRADING_DAYS
from src import metrics


class MetricsTest(unittest.TestCase):
    def test_equity_curve_compounds(self):
        eq = metrics.equity_curve(pd.Series([0.1, -0.5]))
        self.assertAlmostEqual(eq.iloc[0], 1.1)
        self.assertAlmostEqual(eq.iloc[1], 0.55)

    def test_ann_return_constant_daily(self):
        r = 0.001
        n = 10
        ann = metrics.ann_return(pd.Series([r] * n))
        self.assertAlmostEqual(ann, (1 + r) ** TRADING_DAYS - 1, places=6)

    def test_ann_vol_uses_trading_days(self):
        ret = pd.Series([0.01, -0.01, 0.01, -0.01])
        self.assertAlmostEqual(metrics.ann_vol(ret), 0.01 * math.sqrt(TRADING_DAYS))

    def test_sharpe_is_return_over_vol(self):
        ret = pd.Series([0.01, -0.01, 0.01, -0.01])
        expected = metrics.ann_return(ret) / metrics.ann_vol(ret)
        self.assertAlmostEqual(metrics.sharpe(ret, rf=0.0), expected)

    def test_max_drawdown(self):
        # eq = [1.1, 0.55]; peak 1.1 -> dd = 0.55/1.1 - 1 = -0.5
        self.assertAlmostEqual(metrics.max_drawdown(pd.Series([0.1, -0.5])), -0.5)

    def test_trade_stats_counts_runs(self):
        pos = pd.Series([0, 1, 1, 0, 1, 0])
        stats = metrics.trade_stats(pos)
        self.assertEqual(stats["n_trades"], 2)
        self.assertAlmostEqual(stats["avg_hold_days"], 1.5)   # holds [2, 1]
        self.assertAlmostEqual(stats["exposure"], 0.5)        # 3 of 6 days active

    def test_win_rate_only_active_days(self):
        ret = pd.Series([0.0, 0.01, -0.02, 0.0])
        pos = pd.Series([0, 1, 1, 0])
        self.assertAlmostEqual(metrics.win_rate(ret, pos), 0.5)

    def test_win_rate_nan_when_never_active(self):
        ret = pd.Series([0.01, -0.02])
        pos = pd.Series([0, 0])
        self.assertTrue(math.isnan(metrics.win_rate(ret, pos)))

    def test_summarize_keys(self):
        ret = pd.Series([0.01, -0.005, 0.01, 0.0])
        pos = pd.Series([0, 1, 1, 0])
        m = metrics.summarize(ret, pos)
        for k in ("ann_return", "ann_vol", "sharpe", "max_dd", "win_rate",
                  "n_trades", "avg_hold_days", "exposure"):
            self.assertIn(k, m)


if __name__ == "__main__":
    unittest.main()

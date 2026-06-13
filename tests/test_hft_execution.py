import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import ExecutionParams, Pair
from src.hft_execution import simulate_pair_execution


class HftExecutionProxyTest(unittest.TestCase):
    def test_fill_summary_schema_for_position_changes(self):
        dates = pd.date_range("2024-01-01", periods=4, freq="D")
        df = pd.DataFrame({
            "fut_close": [4000.0, 4002.0, 4001.0, 3998.0],
            "etf_close": [4.0, 4.01, 4.00, 3.99],
        }, index=dates)
        pos = pd.Series([0, 1, 1, 0], index=dates)
        pair = Pair(
            name="TEST",
            fut_code="IF.CFX",
            index_code="000300.SH",
            tr_code="H00300.CSI",
            etf_candidates=("510300.SH",),
            multiplier=300,
            start_date="20240101",
        )
        params = ExecutionParams(
            child_slices=2,
            tick_size=0.2,
            spread_ticks=2,
            passive_depth_bps=3.0,
            active_cross_bps=1.5,
            queue_decay=0.35,
            max_passive_wait_slices=1,
            target_notional=1_000_000.0,
        )

        fills, summary = simulate_pair_execution(df, pos, pair, params)

        self.assertEqual(summary["pair"], "TEST")
        self.assertEqual(summary["orders"], 2)
        self.assertEqual(summary["child_fills"], 4)
        self.assertIn("mean_slippage_bps", summary)
        self.assertIn("p95_slippage_bps", summary)
        self.assertTrue(pd.api.types.is_numeric_dtype(fills["total_slippage_bps"]))
        self.assertGreaterEqual(summary["cross_rate"], 0.0)
        self.assertLessEqual(summary["cross_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import RegimeParams
from src.regime import identify_basis_regime


class RegimeIdentificationTest(unittest.TestCase):
    def test_confirmed_premium_neutral_discount_sequence(self):
        dates = pd.date_range("2024-01-01", periods=9, freq="D")
        df = pd.DataFrame({
            "basis_rate": [
                0.0,
                0.012,
                0.013,
                0.014,
                0.003,
                0.001,
                -0.012,
                -0.013,
                -0.014,
            ],
        }, index=dates)
        params = RegimeParams(
            enter_rate=0.01,
            exit_rate=0.002,
            min_confirm_days=3,
            use_dividend_adjusted=True,
        )

        out = identify_basis_regime(df, params)

        self.assertEqual(out["regime"].tolist(), [0, 0, 0, 1, 1, 0, 0, 0, -1])
        self.assertEqual(out["regime_label"].iloc[3], "premium")
        self.assertEqual(out["regime_label"].iloc[-1], "discount")
        self.assertEqual(out["regime_run_days"].iloc[4], 2)


if __name__ == "__main__":
    unittest.main()

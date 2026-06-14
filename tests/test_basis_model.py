import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import basis_model


def _frame() -> pd.DataFrame:
    # dte=73 -> 365/73 = 5x annualisation; raw basis 40 on spot 4000 = 1%.
    return pd.DataFrame({
        "spot": [4000.0, 5000.0],
        "fut_close": [4040.0, 4950.0],
        "dte": [73, 73],
        "div_yield": [0.02, 0.02],
    })


class BasisModelTest(unittest.TestCase):
    def test_raw_basis(self):
        s = basis_model.raw_basis(_frame())
        self.assertAlmostEqual(s.iloc[0], 40.0)
        self.assertAlmostEqual(s.iloc[1], -50.0)

    def test_annualised_basis_rate(self):
        s = basis_model.annualised_basis_rate(_frame())
        # 40/4000 * 5 = 0.05 ; -50/5000 * 5 = -0.05
        self.assertAlmostEqual(s.iloc[0], 0.05)
        self.assertAlmostEqual(s.iloc[1], -0.05)

    def test_dividend_adjusted_adds_yield(self):
        s = basis_model.annualised_basis_rate_divadj(_frame())
        self.assertAlmostEqual(s.iloc[0], 0.07)   # 0.05 + 0.02
        self.assertAlmostEqual(s.iloc[1], -0.03)  # -0.05 + 0.02

    def test_dividend_adjusted_defaults_to_zero_when_missing(self):
        df = _frame().drop(columns=["div_yield"])
        s = basis_model.annualised_basis_rate_divadj(df)
        self.assertAlmostEqual(s.iloc[0], 0.05)

    def test_theory_future_cost_of_carry(self):
        s = basis_model.theory_future(_frame(), rf=0.02)
        # 4000 * (1 + 0.02 * 73/365) = 4000 * 1.004 = 4016
        self.assertAlmostEqual(s.iloc[0], 4016.0)

    def test_no_arb_band_is_symmetric(self):
        lo, hi = basis_model.no_arb_band(0.003)
        self.assertEqual((lo, hi), (-0.003, 0.003))

    def test_with_basis_columns_attaches_all(self):
        out = basis_model.with_basis_columns(_frame(), rf=0.02)
        for col in ("basis", "basis_pct", "basis_rate_raw", "basis_rate", "fut_theory"):
            self.assertIn(col, out.columns)
        self.assertAlmostEqual(out["basis"].iloc[0], 40.0)
        self.assertAlmostEqual(out["basis_pct"].iloc[0], 0.01)
        self.assertAlmostEqual(out["basis_rate_raw"].iloc[0], 0.05)
        self.assertAlmostEqual(out["basis_rate"].iloc[0], 0.07)  # dividend-adjusted
        self.assertAlmostEqual(out["fut_theory"].iloc[0], 4016.0)


if __name__ == "__main__":
    unittest.main()

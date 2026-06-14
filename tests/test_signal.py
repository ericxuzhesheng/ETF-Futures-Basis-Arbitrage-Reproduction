import dataclasses
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import SIGNALS
from src import signal


def _df(rates) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=len(rates), freq="D")
    return pd.DataFrame({"basis_rate": list(rates)}, index=idx)


class ConvergenceTest(unittest.TestCase):
    def test_enters_long_in_premium_and_exits_on_decay(self):
        pos = signal.convergence_position(_df([0.05] * 8 + [-0.02] * 4), SIGNALS)
        self.assertTrue(set(pos.unique()) <= {0, 1})
        self.assertEqual(pos.iloc[7], 1)     # holding the premium regime
        self.assertEqual(pos.iloc[-1], 0)    # flat after basis crosses exit

    def test_no_short_leg_when_short_disallowed(self):
        pos = signal.convergence_position(_df([-0.05] * 12), SIGNALS)  # default: no short
        self.assertEqual(int((pos == -1).sum()), 0)

    def test_short_leg_when_short_allowed(self):
        sig = dataclasses.replace(SIGNALS, allow_short_etf=True)
        pos = signal.convergence_position(_df([-0.05] * 12), sig)
        self.assertTrue((pos == -1).any())


class GalaxyTest(unittest.TestCase):
    def test_high_percentile_enters_long(self):
        pos = signal.galaxy_percentile_position(_df(np.linspace(0.01, 0.06, 80)), SIGNALS)
        self.assertTrue((pos == 1).any())
        self.assertEqual(pos.iloc[-1], 1)


class OrientTest(unittest.TestCase):
    def test_zscore_spike_enters_long(self):
        base = 0.02 + 0.002 * np.sin(np.arange(70))
        pos = signal.orient_zscore_position(_df(list(base) + [0.08] * 3), SIGNALS)
        self.assertTrue((pos == 1).any())

    def test_no_short_on_negative_spike_when_disallowed(self):
        base = -0.02 + 0.002 * np.sin(np.arange(70))
        pos = signal.orient_zscore_position(_df(list(base) + [-0.08] * 3), SIGNALS)
        self.assertEqual(int((pos == -1).sum()), 0)


class CommonContractTest(unittest.TestCase):
    def test_all_generators_return_aligned_int_in_range(self):
        df = _df(np.linspace(-0.03, 0.06, 80))
        for gen in (signal.convergence_position,
                    signal.galaxy_percentile_position,
                    signal.orient_zscore_position):
            pos = gen(df, SIGNALS)
            self.assertTrue(pos.index.equals(df.index))
            self.assertIn(pos.dtype.kind, "iu")
            self.assertTrue(set(pos.unique()) <= {-1, 0, 1})
            self.assertFalse(pos.isna().any())


if __name__ == "__main__":
    unittest.main()

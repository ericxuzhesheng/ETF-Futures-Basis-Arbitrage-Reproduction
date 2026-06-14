import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import ExecutionParams, Pair
from src import l2_snapshot
from src.hft_execution import (
    SnapshotBookProvider, SyntheticBookProvider, simulate_pair_execution,
)


def _write_snapshot(root: Path, code: str, day: str, bid1: float, ask1: float) -> None:
    """Land one depth-5 session file with two intraday rows in the standard schema."""
    ts = pd.to_datetime([f"{day} 09:35:00", f"{day} 14:55:00"])
    row = {"ts": ts}
    for i in range(1, l2_snapshot.LEVELS + 1):
        row[f"bid_px_{i}"] = bid1 - (i - 1) * 0.01
        row[f"bid_sz_{i}"] = 10_000 * i
        row[f"ask_px_{i}"] = ask1 + (i - 1) * 0.01
        row[f"ask_sz_{i}"] = 10_000 * i
    frame = pd.DataFrame(row)
    path = l2_snapshot.snapshot_path(code, day, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


PAIR = Pair(
    name="TEST",
    fut_code="IF.CFX",
    index_code="000300.SH",
    tr_code="H00300.CSI",
    etf_candidates=("510300.SH",),
    multiplier=300,
    start_date="20240101",
)
PARAMS = ExecutionParams(
    child_slices=2, tick_size=0.2, spread_ticks=2, passive_depth_bps=3.0,
    active_cross_bps=1.5, queue_decay=0.35, max_passive_wait_slices=1,
    target_notional=1_000_000.0,
)


class L2SnapshotTest(unittest.TestCase):
    def setUp(self):
        l2_snapshot.clear_cache()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        l2_snapshot.clear_cache()
        self._tmp.cleanup()

    def test_missing_file_returns_none(self):
        self.assertIsNone(l2_snapshot.book_at("510300.SH", "20240102", root=self.root))

    def test_validate_rejects_off_schema(self):
        with self.assertRaises(ValueError):
            l2_snapshot.validate_snapshot_frame(pd.DataFrame({"ts": [1], "bid_px_1": [1.0]}))

    def test_book_at_samples_and_builds_depth(self):
        _write_snapshot(self.root, "510300.SH", "20240102", bid1=4.00, ask1=4.01)
        book = l2_snapshot.book_at("510300.SH", "20240102", "14:55:00",
                                   size_multiplier=1.0, root=self.root)
        self.assertAlmostEqual(book["bid"], 4.00)
        self.assertAlmostEqual(book["ask"], 4.01)
        self.assertAlmostEqual(book["mid"], 4.005)
        self.assertGreater(book["bid_depth_notional"], 0.0)
        self.assertGreater(book["ask_depth_notional"], 0.0)

    def test_snapshot_provider_falls_back_when_unrecorded(self):
        provider = SnapshotBookProvider(root=self.root)
        book = provider.leg_book("20240102", "510300.SH", 4.0, 1.0, False, PARAMS)
        self.assertEqual(book["source"], "snapshot_fallback")

    def test_snapshot_provider_uses_recorded_book(self):
        _write_snapshot(self.root, "510300.SH", "20240102", bid1=4.00, ask1=4.01)
        provider = SnapshotBookProvider(root=self.root)
        book = provider.leg_book("20240102", "510300.SH", 4.0, 1.0, False, PARAMS)
        self.assertEqual(book["source"], "snapshot")
        self.assertAlmostEqual(book["mid"], 4.005)

    def test_simulate_reports_snapshot_coverage(self):
        dates = pd.date_range("2024-01-02", periods=2, freq="D")
        df = pd.DataFrame({"fut_close": [4000.0, 4002.0], "etf_close": [4.0, 4.01]},
                          index=dates)
        pos = pd.Series([1, 0], index=dates)
        # Record only the ETF leg for the first session -> partial coverage.
        _write_snapshot(self.root, "510300.SH", "20240102", bid1=4.00, ask1=4.01)

        provider = SnapshotBookProvider(root=self.root)
        fills, summary = simulate_pair_execution(df, pos, PAIR, PARAMS, provider)

        self.assertIn("snapshot_fill_rate", summary)
        self.assertGreater(summary["snapshot_fill_rate"], 0.0)
        self.assertIn("fut_source", fills.columns)
        self.assertIn("etf_source", fills.columns)
        self.assertTrue((fills["etf_source"] == "snapshot").any())

    def test_synthetic_provider_default_unchanged(self):
        dates = pd.date_range("2024-01-02", periods=2, freq="D")
        df = pd.DataFrame({"fut_close": [4000.0, 4002.0], "etf_close": [4.0, 4.01]},
                          index=dates)
        pos = pd.Series([1, 0], index=dates)
        _, summary = simulate_pair_execution(df, pos, PAIR, PARAMS, SyntheticBookProvider())
        self.assertEqual(summary["snapshot_fill_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()

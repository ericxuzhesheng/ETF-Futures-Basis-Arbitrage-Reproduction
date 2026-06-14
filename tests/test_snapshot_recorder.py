import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import l2_snapshot
from src.snapshot_recorder import (
    TushareRealtimeSource, append_rows, empty_row, record_session,
)


class _FakeSource:
    """Deterministic depth-5 source: best bid/ask drift up each poll."""

    def __init__(self, day="2024-01-02"):
        self.day = day
        self.n = 0

    def fetch(self, code: str) -> dict:
        self.n += 1
        row = empty_row(pd.Timestamp(f"{self.day} 09:3{self.n}:00"))
        row["bid_px_1"] = 4.00 + self.n * 0.001
        row["bid_sz_1"] = 100_000.0
        row["ask_px_1"] = 4.01 + self.n * 0.001
        row["ask_sz_1"] = 120_000.0
        return row


class _Clock:
    def __init__(self, start: datetime, step_s: int = 60):
        self.now = start
        self.step = timedelta(seconds=step_s)

    def __call__(self) -> datetime:
        return self.now

    def tick(self, _seconds: float) -> None:
        self.now += self.step


def _sina_row() -> pd.Series:
    data = {"DATE": "20240102", "TIME": "14:55:00"}
    for i in range(1, 6):
        data[f"B{i}_P"] = 4.00 - (i - 1) * 0.01
        data[f"B{i}_V"] = 10 * i          # 手
        data[f"A{i}_P"] = 4.01 + (i - 1) * 0.01
        data[f"A{i}_V"] = 12 * i
    return pd.Series(data)


class SnapshotRecorderTest(unittest.TestCase):
    def setUp(self):
        l2_snapshot.clear_cache()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        l2_snapshot.clear_cache()
        self._tmp.cleanup()

    def test_tushare_normalize_maps_levels_and_scales_lots(self):
        row = TushareRealtimeSource.normalize(_sina_row())
        self.assertEqual(str(row["ts"]), "2024-01-02 14:55:00")
        self.assertAlmostEqual(row["bid_px_1"], 4.00)
        self.assertAlmostEqual(row["ask_px_5"], 4.05)
        # 10 手 -> 1000 shares
        self.assertAlmostEqual(row["bid_sz_1"], 1000.0)
        self.assertAlmostEqual(row["ask_sz_1"], 1200.0)

    def test_record_session_writes_standard_schema(self):
        clock = _Clock(datetime(2024, 1, 2, 9, 30, 0))
        written = record_session(
            {"510300.SH": _FakeSource()}, root=self.root, interval=1.0,
            until="15:00:00", max_polls=3, now_fn=clock, sleep_fn=clock.tick,
        )
        path = written["510300.SH"]["20240102"]
        self.assertTrue(path.exists())
        frame = pd.read_parquet(path)
        l2_snapshot.validate_snapshot_frame(frame)
        self.assertEqual(len(frame), 3)
        # the recorded file is consumable by the ingestion layer
        book = l2_snapshot.book_at("510300.SH", "20240102", "15:00:00", root=self.root)
        self.assertGreater(book["bid_depth_notional"], 0.0)

    def test_append_rows_dedupes_by_ts(self):
        rows = [empty_row(pd.Timestamp("2024-01-02 09:35:00")) for _ in range(2)]
        rows[0]["bid_px_1"] = rows[0]["ask_px_1"] = 4.0
        rows[1]["bid_px_1"] = rows[1]["ask_px_1"] = 4.0
        append_rows("510300.SH", rows, root=self.root)
        append_rows("510300.SH", rows, root=self.root)  # re-record same ts
        frame = pd.read_parquet(l2_snapshot.snapshot_path("510300.SH", "20240102", self.root))
        self.assertEqual(len(frame), 1)

    def test_failing_source_is_skipped_not_fatal(self):
        class _Bad:
            def fetch(self, code):
                raise RuntimeError("feed down")

        clock = _Clock(datetime(2024, 1, 2, 9, 30, 0))
        errors = []
        written = record_session(
            {"BAD": _Bad()}, root=self.root, max_polls=2,
            now_fn=clock, sleep_fn=clock.tick,
            on_poll=lambda c, r, e: errors.append(e) if e else None,
        )
        self.assertEqual(written, {})
        self.assertEqual(len(errors), 2)


if __name__ == "__main__":
    unittest.main()

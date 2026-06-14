"""Record real L2 snapshots for Track A during the trading session.

Lands the standard depth-5 schema to data/snapshots/<code>/<YYYYMMDD>.parquet,
which run_hftbacktest_proxy.py --use-snapshots then replays.

Run during A-share hours (09:30-11:30 / 13:00-15:00):

    # one-shot validation of the live feed mapping (writes a single snapshot)
    python trackA_execution/record_snapshots.py --etf 510300.SH --once

    # continuous recording until 15:00, sampling every 3s
    python trackA_execution/record_snapshots.py \
        --etf 510300.SH 510500.SH --interval 3 --until 15:00:05

ETF legs record a full 5-level book (tushare/sina). Index-futures 5-level L2
needs a broker CTP feed; wire a futures QuoteSource into --source when available.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.snapshot_recorder import TushareRealtimeSource, record_session  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Record Track A L2 snapshots (depth-5).")
    ap.add_argument("--etf", nargs="+", default=[],
                    help="ETF/stock ts_codes with full 5-level book, e.g. 510300.SH")
    ap.add_argument("--interval", type=float, default=3.0, help="seconds between polls")
    ap.add_argument("--until", default="15:00:05", help="stop time-of-day HH:MM:SS")
    ap.add_argument("--src", default="sina", help="tushare realtime source (sina/dc)")
    ap.add_argument("--once", action="store_true",
                    help="capture a single snapshot then exit (feed validation)")
    args = ap.parse_args()

    codes = list(dict.fromkeys(args.etf))
    if not codes:
        ap.error("provide at least one --etf code")

    source = TushareRealtimeSource(src=args.src)
    sources = {code: source for code in codes}

    polled = {"n": 0, "err": 0}

    def on_poll(code, row, err):
        if err is not None:
            polled["err"] += 1
            print(f"[warn] {code}: {type(err).__name__}: {err}")
        else:
            polled["n"] += 1
            print(f"[poll] {code} @ {row['ts']}  bid1={row['bid_px_1']} ask1={row['ask_px_1']}")

    print(f"recording {codes} | interval={args.interval}s | until {args.until} "
          f"| {'single-shot' if args.once else 'continuous'}")
    written = record_session(
        sources, interval=args.interval, until=args.until,
        max_polls=1 if args.once else None, on_poll=on_poll,
    )

    print(f"\npolls ok={polled['n']} err={polled['err']}")
    if not written:
        print("[no data written] — market likely closed or feed unavailable")
        return
    for code, days in written.items():
        for day, path in days.items():
            print(f"[saved] {code} {day} -> {path}")


if __name__ == "__main__":
    main()

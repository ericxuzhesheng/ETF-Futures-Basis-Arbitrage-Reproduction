"""Binance public-data access for Track B (spot–perpetual basis analogy).

Track B treats Binance **spot vs USDⓈ-M perpetual** as the crypto analog of the
A-share ETF vs index-futures basis: long-spot / short-perp is a cash-and-carry
that earns the **funding rate** (the perpetual's convergence mechanism, paid 8h)
plus any spot–perp premium convergence.

Only free, key-less public REST endpoints are used:
  * spot daily klines   — api.binance.com  /api/v3/klines
  * perp daily klines   — fapi.binance.com /fapi/v1/klines
  * funding-rate history — fapi.binance.com /fapi/v1/fundingRate

Everything is cached to ./data/*.parquet so re-runs are offline and fast.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

SPOT_BASE = "https://api.binance.com"
PERP_BASE = "https://fapi.binance.com"
FUNDING_PER_YEAR = 365  # annualise the *daily* funding sum

_MS_DAY = 86_400_000


def _cache(name: str, builder, refresh: bool = False) -> pd.DataFrame:
    path = DATA_DIR / f"{name}.parquet"
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    df = builder()
    df.to_parquet(path, index=False)
    return df


def _to_ms(date_str: str) -> int:
    return int(pd.Timestamp(date_str, tz="UTC").timestamp() * 1000)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _get(url: str, params: dict, retries: int = 3) -> list:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(full, timeout=15) as resp:
                return json.load(resp)
        except Exception as exc:  # transient network / rate limit
            last = exc
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"Binance request failed: {full}\n{last}")


def _paginate(url: str, params: dict, time_key, start_ms: int, end_ms: int) -> list:
    """Page forward through a time-bounded list endpoint (max 1000 per call)."""
    out: list = []
    cursor = start_ms
    while cursor < end_ms:
        page = _get(url, {**params, "startTime": cursor, "endTime": end_ms, "limit": 1000})
        if not page:
            break
        out.extend(page)
        last_time = time_key(page[-1])
        nxt = last_time + 1
        if nxt <= cursor or len(page) < 1000:
            break
        cursor = nxt
    return out


def _klines_daily(base: str, symbol: str, start: str, end: str | None) -> pd.DataFrame:
    """Daily close keyed by UTC session date -> [date, close]."""
    start_ms, end_ms = _to_ms(start), (_to_ms(end) if end else _now_ms())
    rows = _paginate(f"{base}/api/v3/klines" if base == SPOT_BASE else f"{base}/fapi/v1/klines",
                     {"symbol": symbol, "interval": "1d"}, lambda r: r[0], start_ms, end_ms)
    if not rows:
        return pd.DataFrame(columns=["date", "close"])
    df = pd.DataFrame(rows).iloc[:, [6, 4]]
    df.columns = ["close_time", "close"]
    df["date"] = pd.to_datetime(df["close_time"], unit="ms", utc=True).dt.normalize().dt.tz_localize(None)
    df["close"] = df["close"].astype(float)
    return df[["date", "close"]].drop_duplicates("date").reset_index(drop=True)


def spot_daily(symbol: str, start: str, end: str | None) -> pd.DataFrame:
    return _cache(f"bn_spot_{symbol}_{start}_{end or 'now'}",
                  lambda: _klines_daily(SPOT_BASE, symbol, start, end))


def perp_daily(symbol: str, start: str, end: str | None) -> pd.DataFrame:
    return _cache(f"bn_perp_{symbol}_{start}_{end or 'now'}",
                  lambda: _klines_daily(PERP_BASE, symbol, start, end))


def funding_daily(symbol: str, start: str, end: str | None) -> pd.DataFrame:
    """Sum of the (8h) funding payments per UTC day -> [date, funding_daily]."""
    def build():
        start_ms, end_ms = _to_ms(start), (_to_ms(end) if end else _now_ms())
        rows = _paginate(f"{PERP_BASE}/fapi/v1/fundingRate", {"symbol": symbol},
                         lambda r: r["fundingTime"], start_ms, end_ms)
        if not rows:
            return pd.DataFrame(columns=["date", "funding_daily"])
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True).dt.normalize().dt.tz_localize(None)
        df["fundingRate"] = df["fundingRate"].astype(float)
        return df.groupby("date", as_index=False)["fundingRate"].sum() \
                 .rename(columns={"fundingRate": "funding_daily"})

    return _cache(f"bn_funding_{symbol}_{start}_{end or 'now'}", build)


def build_basis_frame(spot: pd.DataFrame, perp: pd.DataFrame,
                      funding: pd.DataFrame) -> pd.DataFrame:
    """Merge the three legs into a daily basis frame for the Track 0 signal API.

    Adds (mirroring ``basis_model.with_basis_columns``):
      * ``spot`` / ``perp``           daily closes
      * ``premium``                   (perp - spot)/spot
      * ``funding_daily``             summed 8h funding for the day
      * ``basis_rate_raw``            annualised funding (the perp carry)
      * ``basis_rate``                annualised funding + premium (signal carry)
    """
    df = (spot.rename(columns={"close": "spot"})
          .merge(perp.rename(columns={"close": "perp"}), on="date", how="inner")
          .merge(funding, on="date", how="left")
          .sort_values("date").reset_index(drop=True))
    df["funding_daily"] = df["funding_daily"].fillna(0.0)
    df["premium"] = (df["perp"] - df["spot"]) / df["spot"]
    df["basis_rate_raw"] = df["funding_daily"] * FUNDING_PER_YEAR
    df["basis_rate"] = df["basis_rate_raw"] + df["premium"]
    return df.rename(columns={"date": "trade_date"})


def load_crypto_pair(pair, start: str, end: str | None) -> pd.DataFrame:
    """Spot/perp/funding merged into one daily basis frame for ``pair``."""
    spot = spot_daily(pair.spot_symbol, start, end)
    perp = perp_daily(pair.perp_symbol, start, end)
    fund = funding_daily(pair.perp_symbol, start, end)
    return build_basis_frame(spot, perp, fund)

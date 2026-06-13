"""Tushare data access for the ETF / index-futures basis backtest.

Pulls and caches:
  * index daily close  (the futures' settlement target, our spot S)
  * continuous main futures (via fut_mapping + fut_daily, with days-to-expiry)
  * ETF daily close + adjustment factor

Everything is cached to ./data/*.parquet so re-runs are offline and fast.
The Tushare token is read from the gitignored .env (never hardcoded).
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

_PRO = None


def _load_token() -> str:
    """Read TUSHARE_TOKEN from environment, falling back to the .env file."""
    token = os.environ.get("TUSHARE_TOKEN")
    if token:
        return token.strip()
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("TUSHARE_TOKEN="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError(
        "TUSHARE_TOKEN not found. Set the env var or create .env from .env.example."
    )


def pro():
    """Lazily build and cache the tushare pro_api client."""
    global _PRO
    if _PRO is None:
        import tushare as ts

        _PRO = ts.pro_api(_load_token())
    return _PRO


def _cache(name: str, builder, refresh: bool = False) -> pd.DataFrame:
    path = DATA_DIR / f"{name}.parquet"
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    df = builder()
    df.to_parquet(path, index=False)
    return df


# --------------------------------------------------------------------------- #
# Delivery-date logic: CFFEX index futures settle on the 3rd Friday of the
# contract month (rolled to next business day on holidays is ignored here; the
# day-count error is at most a couple of days and washes out in annualisation).
# --------------------------------------------------------------------------- #
def third_friday(year: int, month: int) -> dt.date:
    d = dt.date(year, month, 1)
    # weekday(): Mon=0 ... Fri=4
    first_friday = 1 + (4 - d.weekday()) % 7
    return dt.date(year, month, first_friday + 14)


def contract_delivery(ts_code: str) -> dt.date:
    """e.g. 'IF2403.CFX' -> 2024-03 third Friday."""
    digits = "".join(ch for ch in ts_code.split(".")[0] if ch.isdigit())
    yy, mm = int(digits[:2]), int(digits[2:4])
    return third_friday(2000 + yy, mm)


# --------------------------------------------------------------------------- #
# Public fetchers
# --------------------------------------------------------------------------- #
def index_close(index_code: str, start: str, end: str | None) -> pd.DataFrame:
    """Index daily close -> columns [trade_date(datetime), spot]."""
    def build():
        df = pro().index_daily(ts_code=index_code, start_date=start,
                               end_date=end or _today())
        df = df[["trade_date", "close"]].rename(columns={"close": "spot"})
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        return df.sort_values("trade_date").reset_index(drop=True)

    tag = f"index_{index_code}_{start}_{end or 'now'}"
    return _cache(tag, build)


def etf_close(etf_code: str, start: str, end: str | None) -> pd.DataFrame:
    """ETF daily close + adj factor -> [trade_date, etf_close, etf_adj_close]."""
    def build():
        px = pro().fund_daily(ts_code=etf_code, start_date=start,
                              end_date=end or _today())[["trade_date", "close"]]
        adj = pro().fund_adj(ts_code=etf_code, start_date=start,
                             end_date=end or _today())[["trade_date", "adj_factor"]]
        df = px.merge(adj, on="trade_date", how="left")
        df["adj_factor"] = df["adj_factor"].ffill().bfill()
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.sort_values("trade_date").reset_index(drop=True)
        df["etf_close"] = df["close"]
        df["etf_adj_close"] = df["close"] * df["adj_factor"]
        return df[["trade_date", "etf_close", "etf_adj_close"]]

    tag = f"etf_{etf_code}_{start}_{end or 'now'}"
    return _cache(tag, build)


def etf_panel(etf_code: str, start: str, end: str | None) -> pd.DataFrame:
    """One ETF's daily price/amount/NAV for dynamic selection.

    Returns [trade_date, adj_close, amount, premium] where premium = close/nav-1
    (二级价格相对单位净值的折溢价).
    """
    def build():
        px = pro().fund_daily(ts_code=etf_code, start_date=start,
                              end_date=end or _today())[["trade_date", "close", "amount"]]
        adj = pro().fund_adj(ts_code=etf_code, start_date=start,
                             end_date=end or _today())[["trade_date", "adj_factor"]]
        nav = pro().fund_nav(ts_code=etf_code, start_date=start,
                             end_date=end or _today())[["nav_date", "unit_nav"]] \
            .rename(columns={"nav_date": "trade_date"}) \
            .drop_duplicates("trade_date", keep="last")
        px = px.drop_duplicates("trade_date", keep="last")
        adj = adj.drop_duplicates("trade_date", keep="last")
        df = px.merge(adj, on="trade_date", how="left").merge(nav, on="trade_date", how="left")
        df["adj_factor"] = df["adj_factor"].ffill().bfill()
        df["unit_nav"] = df["unit_nav"].ffill().bfill()
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.sort_values("trade_date").reset_index(drop=True)
        df["adj_close"] = df["close"] * df["adj_factor"]
        df["premium"] = df["close"] / df["unit_nav"] - 1.0
        return df[["trade_date", "adj_close", "amount", "premium"]]

    tag = f"etfpanel_{etf_code}_{start}_{end or 'now'}"
    return _cache(tag, build)


def futures_main(fut_code: str, start: str, end: str | None) -> pd.DataFrame:
    """Continuous main contract via fut_mapping + per-contract fut_daily.

    Returns [trade_date, fut_close, mapping_ts_code, dte] where dte = calendar
    days to that day's mapped contract delivery (used for annualisation).
    """
    def build():
        mp = pro().fut_mapping(ts_code=fut_code, start_date=start,
                               end_date=end or _today())
        mp["trade_date"] = pd.to_datetime(mp["trade_date"])
        mp = mp.sort_values("trade_date").reset_index(drop=True)

        rows = []
        for code in sorted(mp["mapping_ts_code"].unique()):
            d = pro().fut_daily(ts_code=code, start_date=start, end_date=end or _today())
            if d is None or len(d) == 0:
                continue
            d = d[["trade_date", "close"]].copy()
            d["mapping_ts_code"] = code
            rows.append(d)
        daily = pd.concat(rows, ignore_index=True)
        daily["trade_date"] = pd.to_datetime(daily["trade_date"])

        df = mp.merge(daily, on=["trade_date", "mapping_ts_code"], how="left")
        df = df.dropna(subset=["close"]).rename(columns={"close": "fut_close"})
        df["delivery"] = df["mapping_ts_code"].map(
            lambda c: pd.Timestamp(contract_delivery(c)))
        df["dte"] = (df["delivery"] - df["trade_date"]).dt.days.clip(lower=1)
        return df[["trade_date", "fut_close", "mapping_ts_code", "dte"]] \
            .sort_values("trade_date").reset_index(drop=True)

    tag = f"futmain_{fut_code}_{start}_{end or 'now'}"
    return _cache(tag, build)


def dividend_yield(index_code: str, tr_code: str, start: str, end: str | None,
                   window: int) -> pd.DataFrame:
    """Trailing annualised dividend yield from the price vs total-return index gap.

    The 全收益 index reinvests dividends, the price index does not; the rolling
    sum of (TR return - price return) over `window` trading days is the trailing
    annual dividend yield. Used to dividend-adjust the futures basis.
    Returns [trade_date, div_yield].
    """
    def build():
        px = index_close(index_code, start, end).rename(columns={"spot": "px"})
        tr = pro().index_daily(ts_code=tr_code, start_date=start,
                               end_date=end or _today())[["trade_date", "close"]]
        tr["trade_date"] = pd.to_datetime(tr["trade_date"])
        tr = tr.rename(columns={"close": "tr"}).sort_values("trade_date")
        m = px.merge(tr, on="trade_date", how="inner").sort_values("trade_date")
        daily_div = m["tr"].pct_change() - m["px"].pct_change()
        m["div_yield"] = daily_div.rolling(window, min_periods=window // 4) \
                                  .sum().clip(lower=0.0)
        m["div_yield"] = m["div_yield"].bfill()
        return m[["trade_date", "div_yield"]]

    tag = f"divyield_{index_code}_{start}_{end or 'now'}"
    return _cache(tag, build)


def futures_contracts_panel(fut_code: str, start: str, end: str | None) -> pd.DataFrame:
    """Full per-contract daily panel (every liquid contract, all its trading days).

    Unlike futures_main (which keeps only the main contract per day), this returns
    the complete life of each contract so a trade can be held in ONE specific
    contract to its delivery. Returns [trade_date, contract, fut_close, dte].
    """
    def build():
        mp = pro().fut_mapping(ts_code=fut_code, start_date=start, end_date=end or _today())
        rows = []
        for code in sorted(mp["mapping_ts_code"].unique()):
            d = pro().fut_daily(ts_code=code, start_date=start, end_date=end or _today())
            if d is None or len(d) == 0:
                continue
            d = d[["trade_date", "close"]].rename(columns={"close": "fut_close"}).copy()
            d["contract"] = code
            rows.append(d)
        df = pd.concat(rows, ignore_index=True)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df["delivery"] = df["contract"].map(lambda c: pd.Timestamp(contract_delivery(c)))
        df["dte"] = (df["delivery"] - df["trade_date"]).dt.days
        df = df[df["dte"] >= 1]
        return df[["trade_date", "contract", "fut_close", "dte"]] \
            .sort_values(["trade_date", "dte"]).reset_index(drop=True)

    tag = f"futpanel_{fut_code}_{start}_{end or 'now'}"
    return _cache(tag, build)


def load_pair(pair, start: str, end: str | None) -> pd.DataFrame:
    """Merge spot / futures / ETF / dividend-yield onto a common calendar."""
    idx = index_close(pair.index_code, start, end)
    fut = futures_main(pair.fut_code, start, end)
    etf = etf_close(pair.etf_code, start, end)
    div = dividend_yield(pair.index_code, pair.tr_code, start, end,
                         _div_window())
    df = idx.merge(fut, on="trade_date", how="inner") \
            .merge(etf, on="trade_date", how="inner") \
            .merge(div, on="trade_date", how="left")
    df["div_yield"] = df["div_yield"].ffill().bfill().fillna(0.0)
    return df.sort_values("trade_date").reset_index(drop=True)


def _div_window() -> int:
    from config import DIV_YIELD_WINDOW
    return DIV_YIELD_WINDOW


def _today() -> str:
    return dt.date.today().strftime("%Y%m%d")

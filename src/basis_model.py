"""Carry-model basis and no-arbitrage band (华泰柏瑞 / 银河 framework).

Futures fair value under cost-of-carry:
    F_theory = S * (1 + (rf - d) * t/365)          [银河 式]
Annualised basis rate (the signal银河/东证 trade on):
    basis_rate = (F - S) / S * 365 / dte
No-arbitrage band half-width (华泰柏瑞 持有成本+成本参数):
    upper/lower = +/- round_trip_cost (as fraction of S)
"""

from __future__ import annotations

import pandas as pd


def annualised_basis_rate(df: pd.DataFrame) -> pd.Series:
    """(F - S)/S annualised by days-to-expiry. Positive = 期货升水."""
    return (df["fut_close"] - df["spot"]) / df["spot"] * 365.0 / df["dte"]


def raw_basis(df: pd.DataFrame) -> pd.Series:
    """Point basis F - S (not annualised)."""
    return df["fut_close"] - df["spot"]


def theory_future(df: pd.DataFrame, rf: float, div_yield: float = 0.0) -> pd.Series:
    """Cost-of-carry fair futures value."""
    return df["spot"] * (1.0 + (rf - div_yield) * df["dte"] / 365.0)


def no_arb_band(round_trip_cost: float) -> tuple[float, float]:
    """Symmetric no-arbitrage band on the *non-annualised* basis/S, in fraction."""
    return (-round_trip_cost, round_trip_cost)


def with_basis_columns(df: pd.DataFrame, rf: float) -> pd.DataFrame:
    """Attach basis / annualised-rate / fair-value columns to a pair frame."""
    out = df.copy()
    out["basis"] = raw_basis(out)
    out["basis_pct"] = out["basis"] / out["spot"]
    out["basis_rate"] = annualised_basis_rate(out)
    out["fut_theory"] = theory_future(out, rf=rf)
    return out

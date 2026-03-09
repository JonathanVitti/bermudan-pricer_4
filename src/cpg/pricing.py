#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cpg/pricing.py — Moteur de pricing CPG (portefeuille).

Architecture courbe duale (Option 3) :
  - Courbe CDF  = OIS CORRA (base marché) + Spread CDF (funding Desjardins)
  - Pricing P&L  → actualisation sur CDF complet (OIS + Spread)
  - Greeks hedge → bump OIS seulement, spread déterministe fixe
  - TauxCDF se recompose toujours comme ZeroCouponBase + ZeroCouponSpreadCDF

Supporte:
  - COUPON: coupons périodiques + principal à maturité
  - LINEAR ACCRUAL: intérêt simple payé à maturité avec principal
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Callable, Tuple

import numpy as np
import pandas as pd

log = logging.getLogger("cpg.pricing")


# ═══════════════════════════════════════════════════════════════════════════
#  CURVE INTROSPECTION
# ═══════════════════════════════════════════════════════════════════════════

def has_curve_decomposition(curve_df: pd.DataFrame) -> bool:
    """Check if the curve has separate OIS and spread components."""
    return (
        "ZeroCouponBase" in curve_df.columns
        and "ZeroCouponSpreadCDF" in curve_df.columns
        and curve_df["ZeroCouponBase"].notna().any()
        and curve_df["ZeroCouponSpreadCDF"].notna().any()
    )


# ═══════════════════════════════════════════════════════════════════════════
#  DISCOUNT FUNCTION CONSTRUCTION
# ═══════════════════════════════════════════════════════════════════════════

def build_discount_function(
    curve_df: pd.DataFrame,
    component: str = "cdf",
) -> Callable[[float], float]:
    """
    Build a discount factor function from the curve.

    Parameters
    ----------
    curve_df : pd.DataFrame
        Must have: ApproxDays, TauxCDF (%).
        Optionally: ZeroCouponBase (%), ZeroCouponSpreadCDF (%).
    component : str
        "cdf"    : TauxCDF = OIS + Spread  (P&L interne, défaut)
        "ois"    : ZeroCouponBase only     (hedging, Greeks)
        "spread" : ZeroCouponSpreadCDF     (info)

    Returns
    -------
    Callable[[float], float]
        df_func(days) -> discount_factor = exp(-r * t)
    """
    days = curve_df["ApproxDays"].values.astype(float)

    if component == "ois" and has_curve_decomposition(curve_df):
        rates = curve_df["ZeroCouponBase"].values.astype(float)
    elif component == "spread" and has_curve_decomposition(curve_df):
        rates = curve_df["ZeroCouponSpreadCDF"].values.astype(float)
    else:
        rates = curve_df["TauxCDF"].values.astype(float)

    if not np.all(np.diff(days) > 0):
        raise ValueError("Points de courbe non triés par maturité croissante.")

    def discount_factor(target_days: float) -> float:
        t = target_days / 365.0
        if t <= 0:
            return 1.0
        r = np.interp(target_days, days, rates) / 100.0
        return np.exp(-r * t)

    return discount_factor


# ═══════════════════════════════════════════════════════════════════════════
#  CURVE BUMPING — OIS only, spread fixe
# ═══════════════════════════════════════════════════════════════════════════

def bump_curve_ois(curve_df: pd.DataFrame, bump_bp: float) -> pd.DataFrame:
    """
    Parallel bump on OIS component only. Spread CDF stays fixed.
    TauxCDF is bumped by the same amount (since CDF = OIS + Spread,
    bumping OIS by x means CDF also moves by x).

    Note: TauxCDF may not equal OIS + Spread exactly (bootstrap artifacts),
    so we add the delta rather than recomputing from components.
    """
    bumped = curve_df.copy()
    bp_pct = bump_bp / 100.0

    if has_curve_decomposition(bumped):
        bumped["ZeroCouponBase"] = bumped["ZeroCouponBase"] + bp_pct
    # Always bump TauxCDF by the same amount — preserves the OIS/Spread basis
    bumped["TauxCDF"] = bumped["TauxCDF"] + bp_pct

    return bumped


def bump_curve_ois_key_rate(
    curve_df: pd.DataFrame,
    bucket_center_days: int,
    bump_bp: float,
    width_factor: float = 0.5,
) -> pd.DataFrame:
    """
    Triangular key-rate bump on OIS only.
    Spread stays fixed, TauxCDF recomputed.
    """
    bumped = curve_df.copy()
    center = float(bucket_center_days)
    lo = center * (1 - width_factor)
    hi = center * (1 + width_factor)
    bp_pct = bump_bp / 100.0

    weights = np.zeros(len(bumped))
    for i, d in enumerate(bumped["ApproxDays"].values.astype(float)):
        if lo <= d <= center:
            weights[i] = (d - lo) / (center - lo) if center > lo else 1.0
        elif center < d <= hi:
            weights[i] = (hi - d) / (hi - center) if hi > center else 1.0

    if has_curve_decomposition(bumped):
        bumped["ZeroCouponBase"] = bumped["ZeroCouponBase"] + weights * bp_pct
    bumped["TauxCDF"] = bumped["TauxCDF"] + weights * bp_pct

    return bumped


def bump_curve_twist_ois(
    curve_df: pd.DataFrame,
    short_bp: float,
    long_bp: float,
) -> pd.DataFrame:
    """
    Twist (non-parallel) bump on OIS only.
    Linear interpolation: short_bp at day 0, long_bp at max_days.
    Spread stays fixed, TauxCDF recomputed.
    """
    bumped = curve_df.copy()
    max_days = float(bumped["ApproxDays"].max())
    days = bumped["ApproxDays"].values.astype(float)
    frac = days / max_days
    bump = (short_bp * (1.0 - frac) + long_bp * frac) / 100.0

    if has_curve_decomposition(bumped):
        bumped["ZeroCouponBase"] = bumped["ZeroCouponBase"] + bump
    bumped["TauxCDF"] = bumped["TauxCDF"] + bump

    return bumped


# ═══════════════════════════════════════════════════════════════════════════
#  CASHFLOW ENGINE
# ═══════════════════════════════════════════════════════════════════════════

def _year_frac_act365(d1: datetime, d2: datetime) -> float:
    return (d2 - d1).days / 365.0

def _days_between(d1: datetime, d2: datetime) -> int:
    return (d2 - d1).days

def _generate_coupon_dates(
    start: datetime, end: datetime, freq_per_year: int,
) -> List[datetime]:
    if freq_per_year == 0:
        return [end]
    months_per_period = 12 // freq_per_year
    dates = []
    current = start
    while True:
        m = current.month + months_per_period
        y = current.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        d = min(current.day, 28)
        try:
            current = datetime(y, m, d)
        except ValueError:
            current = datetime(y, m, 28)
        if current > end:
            break
        dates.append(current)
    if not dates or dates[-1] != end:
        dates.append(end)
    return dates


def price_coupon_bond(
    notional, coupon_rate, margin, emission, first_coupon,
    maturity, eval_date, freq_per_year, df_func,
) -> Dict[str, Any]:
    """Price a COUPON CPG."""
    total_rate = (coupon_rate + margin) / 100.0
    coupon_dates = _generate_coupon_dates(first_coupon, maturity, freq_per_year)

    pv_coupons = pv_principal = dur_num = 0.0
    cashflows = []

    for i, dt in enumerate(coupon_dates):
        if dt <= eval_date:
            continue
        prev = (first_coupon if first_coupon > eval_date else eval_date) if i == 0 else coupon_dates[i - 1]
        yf = _year_frac_act365(prev, dt)
        coupon_cf = notional * total_rate * yf
        days_to = _days_between(eval_date, dt)
        df = df_func(days_to)
        pv_c = coupon_cf * df
        pv_coupons += pv_c
        dur_num += pv_c * (days_to / 365.0)
        cashflows.append({"Date": dt.strftime("%Y-%m-%d"), "Type": "Coupon",
                          "Amount": round(coupon_cf, 2), "DF": round(df, 8),
                          "PV": round(pv_c, 2), "Days": days_to})

    days_mat = _days_between(eval_date, maturity)
    df_mat = df_func(days_mat)
    pv_principal = notional * df_mat
    dur_num += pv_principal * (days_mat / 365.0)
    cashflows.append({"Date": maturity.strftime("%Y-%m-%d"), "Type": "Principal",
                      "Amount": round(notional, 2), "DF": round(df_mat, 8),
                      "PV": round(pv_principal, 2), "Days": days_mat})

    pv_total = pv_coupons + pv_principal
    duration = dur_num / pv_total if pv_total > 0 else 0.0

    return {"PV": round(pv_total, 2), "PV_Coupons": round(pv_coupons, 2),
            "PV_Principal": round(pv_principal, 2), "DF_Maturity": round(df_mat, 8),
            "Duration_Approx": round(duration, 4), "Nb_Cashflows": len(cashflows),
            "Cashflows": cashflows}


def price_linear_accrual(
    notional, coupon_rate, margin, emission, maturity, eval_date, df_func,
) -> Dict[str, Any]:
    """Price a LINEAR ACCRUAL CPG."""
    total_rate = (coupon_rate + margin) / 100.0
    yf = _year_frac_act365(emission, maturity)
    interest = notional * total_rate * yf
    days_mat = _days_between(eval_date, maturity)
    df_mat = df_func(days_mat)
    total_cf = notional + interest
    pv_total = total_cf * df_mat

    return {"PV": round(pv_total, 2),
            "PV_Coupons": round(interest * df_mat, 2),
            "PV_Principal": round(notional * df_mat, 2),
            "DF_Maturity": round(df_mat, 8),
            "Duration_Approx": round(days_mat / 365.0, 4),
            "Nb_Cashflows": 1,
            "Cashflows": [{"Date": maturity.strftime("%Y-%m-%d"),
                           "Type": "Maturity (P+I)",
                           "Amount": round(total_cf, 2), "DF": round(df_mat, 8),
                           "PV": round(pv_total, 2), "Days": days_mat}]}


def price_single_cpg(row: pd.Series, eval_date: datetime, df_func) -> Dict[str, Any]:
    """Price a single CPG. Dispatches to COUPON or LINEAR ACCRUAL."""
    code = row["CodeTransaction"].upper().strip()
    emission = row["DateEmission"]
    maturity = row["DateEcheanceFinal"]
    first_coupon = row.get("DateEcheanceInitial") or maturity
    notional = float(row["Montant"])
    coupon = float(row["Coupon"])
    margin = float(row.get("Marge", 0))
    freq = int(row["FreqPerYear"])

    if maturity <= eval_date:
        return {"PV": 0.0, "PV_Coupons": 0.0, "PV_Principal": 0.0,
                "DF_Maturity": 0.0, "Duration_Approx": 0.0,
                "Nb_Cashflows": 0, "Status": "MATURED", "Cashflows": []}

    if code == "COUPON":
        result = price_coupon_bond(notional, coupon, margin, emission,
                                   first_coupon, maturity, eval_date, freq, df_func)
    elif code == "LINEAR ACCRUAL":
        result = price_linear_accrual(notional, coupon, margin, emission,
                                       maturity, eval_date, df_func)
    else:
        return {"PV": np.nan, "Status": f"UNSUPPORTED: {code}",
                "PV_Coupons": 0, "PV_Principal": 0, "DF_Maturity": 0,
                "Duration_Approx": 0, "Nb_Cashflows": 0, "Cashflows": []}

    result["Status"] = "OK"
    return result


# ═══════════════════════════════════════════════════════════════════════════
#  PORTFOLIO PRICING
# ═══════════════════════════════════════════════════════════════════════════

def price_cpg_portfolio(
    trades_df: pd.DataFrame,
    curve_df: pd.DataFrame,
    eval_date: str,
    component: str = "cdf",
    config: Optional[dict] = None,
) -> pd.DataFrame:
    """
    Price a full portfolio of CPG trades.

    Parameters
    ----------
    component : str
        "cdf"  → discount on full curve OIS+Spread (P&L interne)
        "ois"  → discount on OIS only (valeur de marché, Greeks de hedging)
    """
    eval_dt = datetime.strptime(eval_date, "%Y-%m-%d")
    df_func = build_discount_function(curve_df, component)

    results = []
    for idx, row in trades_df.iterrows():
        cusip = row.get("CUSIP", "")
        fundserv = row.get("FundServ", "")
        code = row.get("CodeTransaction", "")
        try:
            res = price_single_cpg(row, eval_dt, df_func)
        except Exception as e:
            log.warning(f"Erreur pricing ligne {idx} (CUSIP={cusip}): {e}")
            res = {"PV": np.nan, "PV_Coupons": 0, "PV_Principal": 0,
                   "DF_Maturity": 0, "Duration_Approx": 0,
                   "Nb_Cashflows": 0, "Status": f"ERROR: {e}", "Cashflows": []}

        results.append({
            "EvalDate": eval_date, "CUSIP": cusip, "FundServ": fundserv,
            "CodeTransaction": code,
            "DateEmission": row.get("DateEmission"),
            "DateEcheanceFinal": row.get("DateEcheanceFinal"),
            "Montant": row.get("Montant"), "Coupon": row.get("Coupon"),
            "Marge": row.get("Marge", 0), "Frequence": row.get("Frequence"),
            "PV": res["PV"], "PV_Coupons": res["PV_Coupons"],
            "PV_Principal": res["PV_Principal"],
            "DF_Maturity": res["DF_Maturity"],
            "Duration_Approx": res["Duration_Approx"],
            "Nb_Cashflows": res["Nb_Cashflows"], "Status": res["Status"],
        })

    results_df = pd.DataFrame(results)
    ok = (results_df["Status"] == "OK").sum()
    err = (results_df["Status"].str.startswith("ERROR")).sum()
    mat = (results_df["Status"] == "MATURED").sum()
    log.info(f"Portfolio pricé ({component}): {len(trades_df)} trades -> {ok} OK, {mat} matured, {err} erreurs")
    log.info(f"PV total (OK): {results_df.loc[results_df['Status']=='OK', 'PV'].sum():,.2f} CAD")
    return results_df

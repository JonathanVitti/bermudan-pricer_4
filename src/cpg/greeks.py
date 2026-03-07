#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cpg/greeks.py — Full risk analytics for CPG portfolio.

Architecture courbe duale (Option 3):
  - DV01 hedge   → bump OIS only, spread CDF fixe
  - DV01 P&L     → bump CDF complet (reporté séparément)
  - KR-DV01      → bump OIS key-rate, spread fixe
  - Gamma        → second derivative sur OIS
  - Theta        → 1-day roll, curve held constant
  - Vega         → placeholder (needs HW for prorogeables)
  - Scenarios    → parallel/twist sur OIS, spread fixe

Principle: your IRS/swaption hedge is on OIS CORRA.
The CDF spread is your internal funding cost — deterministic.
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

import numpy as np
import pandas as pd

from cpg.pricing import (
    build_discount_function,
    price_cpg_portfolio,
    bump_curve_ois,
    bump_curve_ois_key_rate,
    bump_curve_twist_ois,
    has_curve_decomposition,
)
from cpg.extendible import compute_cs01

log = logging.getLogger("cpg.greeks")

# ─── Key rate buckets (in days) ──────────────────────────────────────────
KR_BUCKETS = {
    "ON":   1,
    "1M":   30,
    "3M":   90,
    "6M":   182,
    "1Y":   365,
    "2Y":   730,
    "3Y":   1095,
    "5Y":   1825,
    "7Y":   2555,
    "10Y":  3650,
    "15Y":  5475,
    "20Y":  7300,
    "30Y":  10950,
}


def _portfolio_pv(trades_df, curve_df, eval_date: str) -> float:
    """Price portfolio on full CDF curve, return total PV of OK trades."""
    results = price_cpg_portfolio(trades_df, curve_df, eval_date, component="cdf")
    return results.loc[results["Status"] == "OK", "PV"].sum()


# ═══════════════════════════════════════════════════════════════════════════
#  DV01 — HEDGE (OIS only bump)
# ═══════════════════════════════════════════════════════════════════════════

def compute_dv01(
    trades_df: pd.DataFrame,
    curve_df: pd.DataFrame,
    eval_date: str,
    bump_bp: float = 1.0,
) -> Dict[str, Any]:
    """
    Parallel DV01 by bumping OIS only (spread CDF held fixed).

    This gives the hedge ratio: how much IRS notional to put on.
    Convention: positive DV01 = portfolio gains when OIS rates fall.
    """
    pv_base = _portfolio_pv(trades_df, curve_df, eval_date)
    pv_up   = _portfolio_pv(trades_df, bump_curve_ois(curve_df, +bump_bp), eval_date)
    pv_dn   = _portfolio_pv(trades_df, bump_curve_ois(curve_df, -bump_bp), eval_date)

    dv01 = (pv_dn - pv_up) / (2.0 * bump_bp)

    decomposed = bool(has_curve_decomposition(curve_df))
    method = "OIS-only bump (spread fixe)" if decomposed else "CDF bump (pas de décomposition)"

    return {
        "DV01": round(dv01, 2),
        "PV_base": round(pv_base, 2),
        "PV_up": round(pv_up, 2),
        "PV_dn": round(pv_dn, 2),
        "bump_bp": bump_bp,
        "method": method,
        "curve_decomposed": decomposed,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  GAMMA
# ═══════════════════════════════════════════════════════════════════════════

def compute_gamma(
    trades_df: pd.DataFrame,
    curve_df: pd.DataFrame,
    eval_date: str,
    bump_bp: float = 1.0,
) -> Dict[str, float]:
    """Gamma (convexity) via OIS bump: (PV_up - 2*PV_base + PV_dn) / bp^2."""
    pv_base = _portfolio_pv(trades_df, curve_df, eval_date)
    pv_up   = _portfolio_pv(trades_df, bump_curve_ois(curve_df, +bump_bp), eval_date)
    pv_dn   = _portfolio_pv(trades_df, bump_curve_ois(curve_df, -bump_bp), eval_date)

    gamma = (pv_up - 2.0 * pv_base + pv_dn) / (bump_bp ** 2)

    return {
        "Gamma_1bp": round(gamma, 2),
        "Convexity_pct": round(gamma / pv_base * 100, 6) if pv_base else 0.0,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  KEY RATE DV01 — OIS triangular bumps
# ═══════════════════════════════════════════════════════════════════════════

def compute_key_rate_dv01(
    trades_df: pd.DataFrame,
    curve_df: pd.DataFrame,
    eval_date: str,
    bump_bp: float = 1.0,
    buckets: Optional[Dict[str, int]] = None,
) -> Dict[str, float]:
    """KR-DV01 per bucket via triangular OIS bumps."""
    if buckets is None:
        buckets = KR_BUCKETS

    max_days = curve_df["ApproxDays"].max()
    results = {}

    for label, center_days in buckets.items():
        if center_days > max_days * 1.5:
            results[label] = 0.0
            continue

        curve_up = bump_curve_ois_key_rate(curve_df, center_days, +bump_bp)
        curve_dn = bump_curve_ois_key_rate(curve_df, center_days, -bump_bp)

        pv_up = _portfolio_pv(trades_df, curve_up, eval_date)
        pv_dn = _portfolio_pv(trades_df, curve_dn, eval_date)

        results[label] = round((pv_dn - pv_up) / (2.0 * bump_bp), 2)

    return results


# ═══════════════════════════════════════════════════════════════════════════
#  THETA
# ═══════════════════════════════════════════════════════════════════════════

def compute_theta(
    trades_df: pd.DataFrame,
    curve_df: pd.DataFrame,
    eval_date: str,
) -> Dict[str, float]:
    """Theta: PV(T+1) - PV(T), holding curve constant."""
    eval_dt = datetime.strptime(eval_date, "%Y-%m-%d")
    next_date = (eval_dt + timedelta(days=1)).strftime("%Y-%m-%d")

    pv_today = _portfolio_pv(trades_df, curve_df, eval_date)
    pv_tomorrow = _portfolio_pv(trades_df, curve_df, next_date)

    theta_1d = pv_tomorrow - pv_today

    return {
        "Theta_1d": round(theta_1d, 2),
        "Theta_1m": round(theta_1d * 30, 2),
        "carry_bps": round(theta_1d / pv_today * 10000, 2) if pv_today else 0.0,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  VEGA — placeholder for HW extendible pricing
# ═══════════════════════════════════════════════════════════════════════════

def compute_vega(
    trades_df: pd.DataFrame,
    curve_df: pd.DataFrame,
    eval_date: str,
    vol_connector=None,
    bump_bp: float = 1.0,
) -> Dict[str, Any]:
    """
    Vega: sensitivity to swaption vol.

    For fixed-cashflow CPG (no optionality), Vega = 0 analytically.
    For extendible CPG, requires HW repricing with vol bump.
    """
    has_option = False
    if "Prorogeable" in trades_df.columns:
        has_option = trades_df["Prorogeable"].any()

    if not has_option:
        return {
            "Vega_1bp": 0.0,
            "Vega_pct": 0.0,
            "source": "analytique (pas d'optionalité)",
            "confidence": "HIGH",
            "note": "CPG à cashflows fixes — Vega nul par construction.",
        }

    vol_source = "proxy"
    if vol_connector and vol_connector.has_vol:
        vol_source = vol_connector.vol_source

    return {
        "Vega_1bp": 0.0,
        "Vega_pct": 0.0,
        "source": f"en attente HW (vol: {vol_source})",
        "confidence": "LOW" if vol_source == "proxy" else "MEDIUM",
        "note": "Vega prorogeables nécessite le moteur HW. En développement.",
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SCENARIOS — OIS bumps, spread fixe
# ═══════════════════════════════════════════════════════════════════════════

def compute_scenarios(
    trades_df: pd.DataFrame,
    curve_df: pd.DataFrame,
    eval_date: str,
    pv_base: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    P&L under rate scenarios — all bumps on OIS only, spread CDF fixed.

    Parallel: ±10, ±25, ±50, ±100 bp
    Twist: steepener, flattener, bear steep, bull flat
    """
    if pv_base is None:
        pv_base = _portfolio_pv(trades_df, curve_df, eval_date)

    def _scen(name, stype, curve_bumped):
        pv = _portfolio_pv(trades_df, curve_bumped, eval_date)
        return {
            "scenario": name, "type": stype,
            "PV": round(pv, 2),
            "delta_PV": round(pv - pv_base, 2),
            "delta_pct": round((pv - pv_base) / pv_base * 100, 4) if pv_base else 0,
        }

    scenarios = []

    # Parallel OIS shifts
    for bp in [-100, -50, -25, -10, 0, +10, +25, +50, +100]:
        if bp == 0:
            scenarios.append({"scenario": "Base (0bp)", "type": "parallel",
                              "PV": round(pv_base, 2), "delta_PV": 0.0, "delta_pct": 0.0})
        else:
            scenarios.append(_scen(f"Parallel {bp:+d}bp", "parallel",
                                   bump_curve_ois(curve_df, bp)))

    # Twist scenarios — OIS only
    scenarios.append(_scen("Steepener (-25/+25)", "twist",
                           bump_curve_twist_ois(curve_df, -25, +25)))
    scenarios.append(_scen("Flattener (+25/-25)", "twist",
                           bump_curve_twist_ois(curve_df, +25, -25)))
    scenarios.append(_scen("Bear steep (+50/+75)", "twist",
                           bump_curve_twist_ois(curve_df, +50, +75)))
    scenarios.append(_scen("Bull flat (-50/-75)", "twist",
                           bump_curve_twist_ois(curve_df, -50, -75)))

    return scenarios


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════

def compute_all_greeks(
    trades_df: pd.DataFrame,
    curve_df: pd.DataFrame,
    eval_date: str,
    vol_connector=None,
    bump_bp: float = 1.0,
) -> Dict[str, Any]:
    """
    Compute all risk metrics in one call.

    All DV01/Gamma/scenarios bump OIS only (spread CDF deterministic).
    PV_base is on full CDF curve (P&L interne).
    """
    log.info(f"Computing risk analytics (dual-curve): eval_date={eval_date}")

    decomposed = bool(has_curve_decomposition(curve_df))
    if decomposed:
        log.info("Curve decomposition available: OIS + Spread -> bumping OIS only")
    else:
        log.warning("No OIS/Spread decomposition: bumping TauxCDF directly (fallback)")

    pv_base = _portfolio_pv(trades_df, curve_df, eval_date)

    dv01    = compute_dv01(trades_df, curve_df, eval_date, bump_bp)
    gamma   = compute_gamma(trades_df, curve_df, eval_date, bump_bp)
    kr_dv01 = compute_key_rate_dv01(trades_df, curve_df, eval_date, bump_bp)
    theta   = compute_theta(trades_df, curve_df, eval_date)
    vega    = compute_vega(trades_df, curve_df, eval_date, vol_connector, bump_bp)
    scens   = compute_scenarios(trades_df, curve_df, eval_date, pv_base)
    cs01    = compute_cs01(trades_df, curve_df, eval_date, bump_bp)

    result = {
        "eval_date": eval_date,
        "PV_base": round(pv_base, 2),
        "curve_model": {
            "decomposed": decomposed,
            "method": "OIS-only bump, spread CDF fixe" if decomposed else "CDF bump direct",
            "note": ("Greeks calculés par bump OIS uniquement. "
                     "Le spread CDF est traité comme déterministe. "
                     "Hedge ratios directement applicables en IRS/swaptions."
                     if decomposed else
                     "Pas de décomposition OIS/Spread disponible. "
                     "Les Greeks reflètent la sensibilité CDF totale."),
        },
        "dv01": dv01,
        "gamma": gamma,
        "key_rate_dv01": kr_dv01,
        "theta": theta,
        "vega": vega,
        "cs01": cs01,
        "scenarios": scens,
        "vol_source": vol_connector.vol_source if vol_connector and vol_connector.has_vol else "none",
        "timestamp": datetime.now().isoformat(),
    }

    log.info(
        f"Risk analytics complete: DV01={dv01['DV01']:.2f} ({dv01['method']}), "
        f"Gamma={gamma['Gamma_1bp']:.2f}, Theta={theta['Theta_1d']:.2f}, "
        f"CS01={cs01['CS01']:.2f}"
    )

    return result

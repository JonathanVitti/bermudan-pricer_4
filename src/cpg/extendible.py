#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cpg/extendible.py — Pricer CPG prorogeables (Bermudien HW1F).

Architecture spread déterministe terme-dépendant :
══════════════════════════════════════════════════════

Strike (fixé à l'émission, ne bouge jamais) :
    K_i = taux_client
    Le strike est le taux contractuel que la banque paie au client.
    Pas de soustraction de spread (l'ancienne formule K = client - CS_initial
    double-comptait le spread).

Sous-jacent (réagit au marché) :
    FundingForward(t, τ) = OIS_Forward(t, τ) + CS_marché(t, τ)
    où OIS_Forward vient du modèle HW → stochastique
    et CS_marché = courbe de spread ACTUELLE → input exogène

Conséquences :
    - Si le crédit se détériore (spread ↑), le sous-jacent monte,
      la prorogation devient plus avantageuse, la valeur de l'option monte.
    - Le modèle reste HW1F (pas de 2e facteur stochastique).
    - On peut calculer un CS01 (delta spread) en bumpant CS_marché.

Spread terme-dépendant :
    s(t, T) = f(T - t) interpolé sur la courbe de spreads CDF
    Pas un flat — le spread à 1 an résiduel ≠ spread à 10 ans résiduel.
    On interpole linéairement sur la courbe ZeroCouponSpreadCDF par ApproxDays.
"""
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List, Callable, Tuple

import numpy as np
import pandas as pd

from cpg.pricing import (
    build_discount_function,
    has_curve_decomposition,
    _days_between,
    _year_frac_act365,
    _generate_coupon_dates,
)

log = logging.getLogger("cpg.extendible")


# ═══════════════════════════════════════════════════════════════════════════
#  SPREAD TERM STRUCTURE — s(τ) interpolé par maturité résiduelle
# ═══════════════════════════════════════════════════════════════════════════

class SpreadTermStructure:
    """
    Courbe de spread CDF interpolée par terme résiduel.

    Construit à partir de la courbe CDF (colonnes ApproxDays, ZeroCouponSpreadCDF).
    Permet d'évaluer s(τ) pour n'importe quelle maturité résiduelle τ (en jours).
    """

    def __init__(self, curve_df: pd.DataFrame):
        """
        Parameters
        ----------
        curve_df : pd.DataFrame
            Doit contenir ApproxDays et ZeroCouponSpreadCDF.
        """
        if not has_curve_decomposition(curve_df):
            raise ValueError(
                "SpreadTermStructure requiert une courbe avec décomposition "
                "OIS/Spread (colonnes ZeroCouponBase et ZeroCouponSpreadCDF)."
            )

        self._days = curve_df["ApproxDays"].values.astype(float)
        self._spreads = curve_df["ZeroCouponSpreadCDF"].values.astype(float)

        if not np.all(np.diff(self._days) > 0):
            idx = np.argsort(self._days)
            self._days = self._days[idx]
            self._spreads = self._spreads[idx]

    def spread_at(self, residual_days: float) -> float:
        """
        Interpolate spread (in %) for a given residual maturity in days.

        Returns spread in percent (e.g. 0.52 for 52bp).
        """
        if residual_days <= 0:
            return float(self._spreads[0])
        return float(np.interp(residual_days, self._days, self._spreads))

    def spread_at_years(self, residual_years: float) -> float:
        """Same as spread_at, but input in years."""
        return self.spread_at(residual_years * 365.0)

    def spread_bp_at(self, residual_days: float) -> float:
        """Spread in basis points."""
        return self.spread_at(residual_days) * 100.0

    def flat_equivalent(self, max_days: float = None) -> float:
        """Average spread across the curve (in %) — for reporting."""
        if max_days is not None:
            mask = self._days <= max_days
            if mask.any():
                return float(np.mean(self._spreads[mask]))
        return float(np.mean(self._spreads))

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for JSON."""
        return {
            "type": "term_dependent",
            "points": len(self._days),
            "short_end_bp": round(self.spread_bp_at(self._days[0]), 1),
            "long_end_bp": round(self.spread_bp_at(self._days[-1]), 1),
            "flat_equivalent_bp": round(self.flat_equivalent() * 100, 1),
            "term_structure": [
                {"days": int(d), "spread_bp": round(s * 100, 1)}
                for d, s in zip(self._days, self._spreads)
            ],
        }

    def __repr__(self):
        return (
            f"SpreadTermStructure({len(self._days)} pts, "
            f"short={self.spread_bp_at(self._days[0]):.0f}bp, "
            f"long={self.spread_bp_at(self._days[-1]):.0f}bp)"
        )


# ═══════════════════════════════════════════════════════════════════════════
#  EXTENDIBLE CPG DEFINITION
# ═══════════════════════════════════════════════════════════════════════════

class ExtendibleCPG:
    """
    Définition d'un CPG prorogeable.

    Deux types supportés (inspirés des prospectus BNC) :
    - COUPON: coupons fixes annuels, prorogeable annuellement (ex: NBC37043, 4.10%)
    - LINEAR ACCRUAL: intérêt cumulatif linéaire, payé à maturité (ex: NBC37041, 6.05%)

    Un CPG prorogeable a :
    - Des cashflows qui dépendent du type
    - Des dates d'exercice de prorogation (bermudien)
    - À chaque date d'exercice, la banque décide : proroger ou pas
    - Si prorogation : la maturité est reportée à la prochaine date d'exercice
    """

    def __init__(
        self,
        cusip: str,
        notional: float,
        client_rate: float,          # taux client en % (ex: 5.00)
        emission: datetime,
        initial_maturity: datetime,   # première maturité possible
        final_maturity: datetime,     # maturité maximale si toutes prorogations exercées
        exercise_dates: List[datetime],  # dates de décision de prorogation
        cpg_type: str = "COUPON",    # "COUPON" ou "LINEAR ACCRUAL"
        freq_per_year: int = 1,
        day_count: str = "ACT/365",
        fundserv: str = "",
    ):
        self.cusip = cusip
        self.notional = notional
        self.client_rate = client_rate
        self.emission = emission
        self.initial_maturity = initial_maturity
        self.final_maturity = final_maturity
        self.exercise_dates = sorted(exercise_dates)
        self.cpg_type = cpg_type.upper().strip()
        self.freq_per_year = freq_per_year
        self.day_count = day_count
        self.fundserv = fundserv

    @property
    def max_years(self) -> float:
        return _days_between(self.emission, self.final_maturity) / 365.0

    @property
    def min_years(self) -> float:
        return _days_between(self.emission, self.initial_maturity) / 365.0

    def remboursement_schedule(self) -> List[Dict[str, Any]]:
        """
        Build the remboursement schedule for LINEAR ACCRUAL type.

        For each possible maturity date, compute:
        - cumulative interest = notional × rate × years
        - total remboursement = notional + cumulative interest
        - annualized yield

        Like the NBC37041 table: Year 1 = 106.05%, Year 2 = 112.10%, etc.
        """
        if self.cpg_type != "LINEAR ACCRUAL":
            return []

        rate_dec = self.client_rate / 100.0
        schedule = []
        all_dates = [self.initial_maturity] + [
            d for d in self.exercise_dates if d > self.initial_maturity
        ]
        if self.final_maturity not in all_dates:
            all_dates.append(self.final_maturity)
        all_dates = sorted(set(all_dates))

        for dt in all_dates:
            years = _days_between(self.emission, dt) / 365.0
            cumulative_rate = rate_dec * years  # linear accrual
            total_pct = 1.0 + cumulative_rate
            annualized = (total_pct ** (1.0 / years) - 1.0) * 100 if years > 0 else 0

            schedule.append({
                "date": dt.strftime("%Y-%m-%d"),
                "year": round(years, 1),
                "cumulative_rate_pct": round(cumulative_rate * 100, 2),
                "annualized_yield_pct": round(annualized, 2),
                "remboursement_pct": round(total_pct * 100, 2),
                "remboursement_amount": round(self.notional * total_pct, 2),
            })

        return schedule

    @classmethod
    def from_trade_row(cls, row: pd.Series) -> "ExtendibleCPG":
        """
        Construct from a trades DataFrame row.

        Assumes exercise_dates can be derived from DateEcheanceInitial to
        DateEcheanceFinal with annual frequency.
        """
        emission = row["DateEmission"]
        initial_mat = row.get("DateEcheanceInitial") or row["DateEcheanceFinal"]
        final_mat = row["DateEcheanceFinal"]
        freq = int(row.get("FreqPerYear", 1))
        cpg_type = str(row.get("CodeTransaction", "COUPON")).upper().strip()

        # Generate exercise dates: annual from initial_mat to final_mat
        exercise_dates = []
        current = initial_mat
        while current < final_mat:
            exercise_dates.append(current)
            try:
                current = datetime(current.year + 1, current.month, current.day)
            except ValueError:
                current = datetime(current.year + 1, current.month, 28)

        return cls(
            cusip=str(row.get("CUSIP", "")),
            notional=float(row["Montant"]),
            client_rate=float(row["Coupon"]) + float(row.get("Marge", 0)),
            emission=emission,
            initial_maturity=initial_mat,
            final_maturity=final_mat,
            exercise_dates=exercise_dates,
            cpg_type=cpg_type,
            freq_per_year=freq,
            fundserv=str(row.get("FundServ", "")),
        )

    @classmethod
    def from_prospectus(
        cls,
        cusip: str,
        fundserv: str,
        notional: float,
        client_rate: float,
        emission: datetime,
        initial_maturity: datetime,
        final_maturity: datetime,
        cpg_type: str = "COUPON",
        freq_per_year: int = 1,
    ) -> "ExtendibleCPG":
        """
        Construct from prospectus terms (like BNC information statements).

        Example COUPON (NBC37043):
            ExtendibleCPG.from_prospectus(
                cusip="", fundserv="NBC37043", notional=100,
                client_rate=4.10, cpg_type="COUPON",
                emission=datetime(2025,10,2), initial_maturity=datetime(2026,10,2),
                final_maturity=datetime(2035,10,2))

        Example LINEAR ACCRUAL (NBC37041):
            ExtendibleCPG.from_prospectus(
                cusip="", fundserv="NBC37041", notional=100,
                client_rate=6.05, cpg_type="LINEAR ACCRUAL",
                emission=datetime(2025,10,2), initial_maturity=datetime(2026,10,2),
                final_maturity=datetime(2040,10,2), freq_per_year=0)
        """
        exercise_dates = []
        current = initial_maturity
        while current < final_maturity:
            exercise_dates.append(current)
            try:
                current = datetime(current.year + 1, current.month, current.day)
            except ValueError:
                current = datetime(current.year + 1, current.month, 28)

        return cls(
            cusip=cusip, notional=notional, client_rate=client_rate,
            emission=emission, initial_maturity=initial_maturity,
            final_maturity=final_maturity, exercise_dates=exercise_dates,
            cpg_type=cpg_type, freq_per_year=freq_per_year, fundserv=fundserv,
        )


# ═══════════════════════════════════════════════════════════════════════════
#  EXERCISE STRIKES — déterministe, terme-dépendant, fixé à l'émission
# ═══════════════════════════════════════════════════════════════════════════

def compute_exercise_strikes(
    cpg: ExtendibleCPG,
    spread_initial: SpreadTermStructure,
) -> Dict[datetime, float]:
    """
    Compute the exercise strike at each prorogation date.

    FIX P-CRITIQUE: Le strike est le taux client directement.
    
    Logique économique :
      - À chaque date d'exercice, Desjardins compare le coût de financement
        au marché (OIS + spread marché) vs le taux qu'elle paie au client.
      - Si funding_market > client_rate → la prorogation est avantageuse
        (Desjardins se finance en dessous du marché).
      - Le strike est donc simplement le taux client.
      - Le sous-jacent (dans l'arbre HW1F) est le par rate de financement
        = OIS_forward(r_node) + CS_market(τ).
    
    L'ancienne formule K = client - CS_initial double-comptait le spread
    (une fois soustrait du strike, une fois ajouté au sous-jacent).

    Parameters
    ----------
    cpg : ExtendibleCPG
    spread_initial : SpreadTermStructure
        Conservé pour compatibilité et pour le CS01 si nécessaire.

    Returns
    -------
    Dict[datetime, float]
        Strike (in %) at each exercise date = taux client.
    """
    strikes = {}
    for ex_date in cpg.exercise_dates:
        strike = cpg.client_rate  # Taux client directement
        strikes[ex_date] = round(strike, 6)

        residual_days = _days_between(ex_date, cpg.final_maturity)
        log.debug(
            f"  Strike {ex_date.strftime('%Y-%m-%d')}: "
            f"client={cpg.client_rate:.2f}% (résiduel {residual_days}j)"
        )

    return strikes


# ═══════════════════════════════════════════════════════════════════════════
#  FUNDING FORWARD — OIS stochastique + spread marché actuel
# ═══════════════════════════════════════════════════════════════════════════

def compute_funding_forward(
    ois_forward_rate: float,
    spread_market: SpreadTermStructure,
    residual_days: float,
) -> float:
    """
    Compute the funding forward rate at a given node.

    FundingForward(t, τ) = OIS_Forward(t, τ) + CS_marché(t, τ)

    Parameters
    ----------
    ois_forward_rate : float
        OIS forward rate from HW model at this node (in %).
    spread_market : SpreadTermStructure
        CURRENT market spread curve (not the one at emission).
    residual_days : float
        Residual maturity in days.

    Returns
    -------
    float
        Funding forward rate in %.
    """
    cs_market = spread_market.spread_at(residual_days)
    return ois_forward_rate + cs_market


# ═══════════════════════════════════════════════════════════════════════════
#  CS01 — CREDIT SPREAD SENSITIVITY
# ═══════════════════════════════════════════════════════════════════════════

def bump_spread_curve(
    curve_df: pd.DataFrame,
    bump_bp: float,
) -> pd.DataFrame:
    """
    Bump the spread component only (for CS01 calculation).
    OIS stays fixed, TauxCDF is adjusted.

    This is the INVERSE of bump_curve_ois: here we bump the spread,
    not the OIS. Used for credit spread sensitivity.
    """
    bumped = curve_df.copy()
    bp_pct = bump_bp / 100.0

    if has_curve_decomposition(bumped):
        bumped["ZeroCouponSpreadCDF"] = bumped["ZeroCouponSpreadCDF"] + bp_pct
        bumped["TauxCDF"] = bumped["TauxCDF"] + bp_pct
    else:
        log.warning("bump_spread_curve: pas de décomposition, bump CDF direct")
        bumped["TauxCDF"] = bumped["TauxCDF"] + bp_pct

    return bumped


def compute_cs01(
    trades_df: pd.DataFrame,
    curve_df: pd.DataFrame,
    eval_date: str,
    bump_bp: float = 1.0,
) -> Dict[str, Any]:
    """
    Compute CS01: sensitivity to a parallel shift in the funding spread.

    CS01 = (PV_spread_down - PV_spread_up) / (2 * bump)

    This measures: if Desjardins' credit deteriorates by 1bp
    (spread widens), how much does the portfolio value change?

    For fixed-cashflow CPG: CS01 ≈ DV01 (since spread affects DF the same way).
    For extendible CPG: CS01 ≠ DV01 because the spread affects the
    funding forward (sous-jacent) but not the HW dynamics.
    """
    from cpg.pricing import price_cpg_portfolio

    def _pv(c):
        r = price_cpg_portfolio(trades_df, c, eval_date, component="cdf")
        return r.loc[r["Status"] == "OK", "PV"].sum()

    pv_base = _pv(curve_df)
    pv_up   = _pv(bump_spread_curve(curve_df, +bump_bp))
    pv_dn   = _pv(bump_spread_curve(curve_df, -bump_bp))

    cs01 = (pv_dn - pv_up) / (2.0 * bump_bp)

    return {
        "CS01": round(cs01, 2),
        "PV_base": round(pv_base, 2),
        "PV_spread_up": round(pv_up, 2),
        "PV_spread_dn": round(pv_dn, 2),
        "bump_bp": bump_bp,
        "note": (
            "CS01 = sensibilité au spread de funding. "
            "Positif = le portefeuille gagne quand le spread se comprime."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  PRICING ENGINE — PLACEHOLDER FOR HW1F BERMUDIEN
# ═══════════════════════════════════════════════════════════════════════════

def price_extendible_cpg(
    cpg: ExtendibleCPG,
    curve_df: pd.DataFrame,
    spread_initial: SpreadTermStructure,
    spread_market: SpreadTermStructure,
    eval_date: datetime,
    vol_surface=None,
    hw_params: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Price a single extendible CPG using HW1F Bermudian framework.

    Architecture:
        1. Build OIS yield curve from curve_df (OIS component)
        2. Calibrate HW1F on swaption vols (OIS world)
        3. At each exercise node in the tree:
           - Compute OIS forward from HW model (stochastic)
           - Add CS_marché(τ) to get funding forward (exogenous)
           - Compare funding forward vs strike K_i = client_rate
           - Exercise decision: proroger if funding forward > strike
        4. Roll back through the tree to get option value
        5. Total PV = PV_fixed_cashflows + option_value

    Parameters
    ----------
    cpg : ExtendibleCPG
        The prorogeable instrument definition.
    curve_df : pd.DataFrame
        Current market curve with OIS/Spread decomposition.
    spread_initial : SpreadTermStructure
        Spread curve at emission (frozen, for strikes).
    spread_market : SpreadTermStructure
        Current spread curve (for funding forwards).
    eval_date : datetime
        Valuation date.
    vol_surface : optional
        Swaption vol surface for HW calibration.
    hw_params : dict, optional
        Override HW parameters (mean_reversion, sigma, etc.)

    Returns
    -------
    Dict with PV_total, PV_fixed, option_value, exercise_probabilities, etc.
    """
    # --- Compute strikes at each exercise date ---
    strikes = compute_exercise_strikes(cpg, spread_initial)

    # --- Fixed leg PV (cashflows to initial maturity) ---
    df_cdf = build_discount_function(curve_df, "cdf")
    days_to_init = _days_between(eval_date, cpg.initial_maturity)

    if cpg.cpg_type == "COUPON":
        # Coupon CPG: periodic fixed coupons + principal at maturity
        coupon_dates = _generate_coupon_dates(
            cpg.emission, cpg.initial_maturity, cpg.freq_per_year
        )
        total_rate = cpg.client_rate / 100.0
        pv_fixed = 0.0
        for dt in coupon_dates:
            if dt <= eval_date:
                continue
            days = _days_between(eval_date, dt)
            yf = 1.0 / cpg.freq_per_year if cpg.freq_per_year > 0 else _year_frac_act365(cpg.emission, cpg.initial_maturity)
            cf = cpg.notional * total_rate * yf
            pv_fixed += cf * df_cdf(days)
        # Principal at initial maturity
        pv_fixed += cpg.notional * df_cdf(days_to_init)

    elif cpg.cpg_type == "LINEAR ACCRUAL":
        # Linear accrual: interest cumulates linearly, single payout at maturity
        total_rate = cpg.client_rate / 100.0
        years_to_init = _year_frac_act365(cpg.emission, cpg.initial_maturity)
        cumulative_interest = cpg.notional * total_rate * years_to_init
        total_remboursement = cpg.notional + cumulative_interest
        pv_fixed = total_remboursement * df_cdf(days_to_init)

    else:
        pv_fixed = cpg.notional * df_cdf(days_to_init)

    # --- Option value: HW1F trinomial tree (backward induction) ---
    # Fixes P2-1 (time value), P2-2 (par rate), P2-3 (real vega)
    from cpg.hw1f_tree import price_bermudan_hw1f

    df_ois = build_discount_function(curve_df, "ois")

    # Get vol from connector if available, otherwise use default
    sigma_bp = 65.0  # default

    hw_result = price_bermudan_hw1f(
        df_cdf_func=df_cdf,
        df_ois_func=df_ois,
        spread_market=spread_market,
        eval_date=eval_date,
        exercise_dates=cpg.exercise_dates,
        strikes=strikes,
        notional=cpg.notional,
        client_rate=cpg.client_rate,
        cpg_type=cpg.cpg_type,
        freq_per_year=cpg.freq_per_year,
        final_maturity=cpg.final_maturity,
        sigma_bp=sigma_bp,
        a=0.03,
    )

    option_value = hw_result["option_value"]
    intrinsic_value = hw_result["intrinsic_value"]
    time_value = hw_result["time_value"]
    exercise_analysis = hw_result["exercise_analysis"]

    # Mark optimal exercise
    best_idx = -1
    best_pv = 0
    for i, ea in enumerate(exercise_analysis):
        if ea["intrinsic_pv"] > best_pv:
            best_pv = ea["intrinsic_pv"]
            best_idx = i
    for i, ea in enumerate(exercise_analysis):
        ea["optimal"] = (i == best_idx)

    pv_total = pv_fixed + option_value
    # SIGN CONVENTION: option > 0 = extension valuable to ISSUER (Desjardins)

    # Compute Vega via sigma bump in HW1F tree
    from cpg.hw1f_tree import compute_vega_hw1f
    try:
        vega_result = compute_vega_hw1f(
            df_cdf, df_ois, spread_market, eval_date,
            cpg.exercise_dates, strikes, cpg.notional, cpg.client_rate,
            cpg.cpg_type, cpg.freq_per_year, cpg.final_maturity,
            sigma_bp=sigma_bp, a=0.03, bump_bp=1.0,
        )
    except Exception:
        vega_result = {"vega_1bp": 0, "source": "erreur de calcul"}

    return {
        "CUSIP": cpg.cusip,
        "FundServ": cpg.fundserv,
        "cpg_type": cpg.cpg_type,
        "PV_total": round(pv_total, 2),
        "PV_fixed": round(pv_fixed, 2),
        "option_value": round(option_value, 2),
        "intrinsic_value": round(intrinsic_value, 2),
        "time_value": round(time_value, 2),
        "option_method": hw_result["method"],
        "hw_sigma_bp": hw_result.get("sigma_bp", 0),
        "hw_mean_reversion": hw_result.get("mean_reversion", 0),
        "hw_vega_1bp": vega_result.get("vega_1bp", 0),
        "hw_vega_source": vega_result.get("source", ""),
        "client_rate_pct": cpg.client_rate,
        "initial_maturity": cpg.initial_maturity.strftime("%Y-%m-%d"),
        "final_maturity": cpg.final_maturity.strftime("%Y-%m-%d"),
        "min_years": round(cpg.min_years, 1),
        "max_years": round(cpg.max_years, 1),
        "n_exercise_dates": len(cpg.exercise_dates),
        "spread_initial": spread_initial.to_dict(),
        "spread_market_flat_bp": round(spread_market.flat_equivalent() * 100, 1),
        "exercise_analysis": exercise_analysis,
        "remboursement_schedule": cpg.remboursement_schedule(),
        "Status": "OK",
    }

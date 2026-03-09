#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cpg/hw1f_tree.py — Arbre trinomial Hull-White 1 facteur pour CPG prorogeables.

Implémentation basée sur Hull & White (1994), « Numerical Procedures for
Implementing Term Structure Models: Single-Factor Models ».

L'arbre est calibré sur la structure à terme initiale (courbe CDF) et produit :
  - La valeur bermudienne de l'option de prorogation (avec time value)
  - L'exercice optimal par backward induction
  - Le véga (sensibilité à σ) calculé par bump du paramètre de vol dans l'arbre

Conventions :
  - Short rate r(t) sous la mesure risque-neutre
  - dr = (θ(t) - a·r)dt + σ·dW
  - a = mean reversion, σ = volatilité (en décimal, pas en pb)
  - θ(t) calibré pour reproduire la courbe initiale
  - Exercice : comparaison par rate de financement vs strike (pas ZC forward)
"""
import logging
from datetime import datetime
from typing import List, Tuple, Dict, Any, Optional

import numpy as np

log = logging.getLogger("cpg.hw1f_tree")


def price_bermudan_hw1f(
    df_cdf_func,        # discount factor function CDF (days → DF)
    df_ois_func,        # discount factor function OIS (days → DF)
    spread_market,      # SpreadTermStructure (current market)
    eval_date: datetime,
    exercise_dates: List[datetime],
    strikes: Dict[datetime, float],   # strike at each exercise date (%)
    notional: float,
    client_rate: float,  # % annuel
    cpg_type: str,       # "COUPON" or "LINEAR ACCRUAL"
    freq_per_year: int,
    final_maturity: datetime,
    sigma_bp: float = 65.0,   # vol en pb
    a: float = 0.03,          # mean reversion
    n_per_year: int = 12,     # pas de temps par an
) -> Dict[str, Any]:
    """
    Price l'option bermudienne de prorogation via arbre trinomial HW1F.

    Returns dict with:
        option_value: valeur totale (intrinsic + time value)
        intrinsic_value: max intrinsic (comme avant)
        time_value: option_value - intrinsic_value
        exercise_analysis: liste par date d'exercice
    """
    sigma = sigma_bp / 10000.0  # bp → décimal

    # ── Grille temporelle ──
    future_ex = [d for d in exercise_dates if d > eval_date]
    if not future_ex:
        return {"option_value": 0, "intrinsic_value": 0, "time_value": 0,
                "exercise_analysis": [], "method": "HW1F (aucun exercice futur)"}

    T_max = (future_ex[-1] - eval_date).days / 365.0
    dt = 1.0 / n_per_year
    N = max(int(T_max / dt) + 1, 2)
    dt = T_max / N  # ajuster pour tomber exactement sur T_max
    times = np.array([i * dt for i in range(N + 1)])

    # Map exercise dates to nearest time step
    ex_steps = {}
    for d in future_ex:
        t = (d - eval_date).days / 365.0
        step = int(round(t / dt))
        step = min(max(step, 1), N)
        ex_steps[step] = d

    # ── Paramètres de l'arbre ──
    dr = sigma * np.sqrt(3.0 * dt)
    if dr < 1e-12:
        dr = 1e-6  # safety
    j_max = max(int(np.ceil(0.184 / (a * dt))), 1)

    n_nodes = 2 * j_max + 1  # -j_max to +j_max

    # ── Taux forward instantanés depuis la courbe ──
    def _inst_fwd(t_years):
        """Forward instantané depuis la courbe de DF."""
        with np.errstate(invalid='ignore', divide='ignore'):
            if t_years <= 0:
                t_years = 0.01
            eps = max(0.001, t_years * 0.01)
            t1 = max(t_years - eps, 0.001)
            t2 = t_years + eps
            df1 = df_cdf_func(t1 * 365)
            df2 = df_cdf_func(t2 * 365)
            if df1 <= 1e-15 or df2 <= 1e-15 or df1 <= df2 * 0.5:
                df_t = df_cdf_func(t_years * 365)
                if df_t > 1e-15:
                    return -np.log(df_t) / t_years
                return 0.03
            fwd = -np.log(df2 / df1) / (t2 - t1)
            if not np.isfinite(fwd):
                return 0.03
            return max(0, min(fwd, 0.20))

    # ── Calibration de θ(t) — méthode Arrow-Debreu ──
    # On construit l'arbre en avant pour déterminer θ(t) à chaque pas
    # de sorte que les prix Arrow-Debreu reproduisent les DF de marché.

    # Alpha shift: r(i,j) = alpha(i) + j * dr
    alpha = np.zeros(N + 1)
    alpha[0] = _inst_fwd(0)

    # Probabilités de transition pour le nœud j
    def _probs(j):
        """Probabilités (pu, pm, pd) pour le nœud j."""
        eta = a * j * dt
        pu = 1/6 + (j**2 * a**2 * dt**2 - j * a * dt) / 2
        pm = 2/3 - j**2 * a**2 * dt**2
        pd = 1/6 + (j**2 * a**2 * dt**2 + j * a * dt) / 2
        # Clip pour stabilité
        pu = np.clip(pu, 0.01, 0.98)
        pd = np.clip(pd, 0.01, 0.98)
        pm = 1.0 - pu - pd
        pm = max(pm, 0.01)
        total = pu + pm + pd
        return pu / total, pm / total, pd / total

    # ── Propagation avant : prix Arrow-Debreu Q(i,j) ──
    Q = [np.zeros(n_nodes) for _ in range(N + 1)]
    Q[0][j_max] = 1.0  # nœud central à t=0

    for i in range(N):
        # Calibrer alpha[i+1] pour matcher le DF de marché
        target_df = df_cdf_func(times[i + 1] * 365)

        # Calculer la somme pondérée des Q pour le prochain pas
        # en fonction de alpha[i+1] (inconnue)
        # On résout: sum_j Q(i,j) * exp(-r(i,j)*dt) = target_df / sum déjà actualisé
        # Approche itérative simple : on fixe alpha(i+1) = forward rate
        alpha_guess = _inst_fwd(times[i + 1])

        # Calcul des Q(i+1,j) avec alpha_guess
        Q_next = np.zeros(n_nodes)
        for jj in range(-j_max, j_max + 1):
            j_idx = jj + j_max
            if Q[i][j_idx] < 1e-15:
                continue
            r_ij = alpha[i] + jj * dr
            discount = np.exp(-r_ij * dt)
            pu, pm, pd = _probs(jj)

            # Successors (with clamping)
            for dj, p in [(1, pu), (0, pm), (-1, pd)]:
                jnew = jj + dj
                jnew = max(-j_max, min(j_max, jnew))
                Q_next[jnew + j_max] += Q[i][j_idx] * discount * p

        # Ajuster alpha pour matcher le DF
        sum_q = np.sum(Q_next)
        if sum_q > 1e-15 and target_df > 1e-15:
            # Q_next est calculé avec alpha_guess. Pour corriger :
            # Le vrai DF = sum(Q_next * exp(-shift*dt)) = target_df
            # → shift tel que sum_q * exp(-shift*dt) ≈ target_df
            # → shift = -log(target_df / sum_q) / dt
            correction = -np.log(target_df / sum_q) / dt
            alpha[i + 1] = alpha_guess + correction
            # Recalculer Q_next avec le bon alpha
            # En pratique, la correction est petite, on la propage simplement
            Q_next *= (target_df / sum_q)

        Q[i + 1] = Q_next
        if not np.isfinite(alpha[i + 1]):
            alpha[i + 1] = alpha_guess
        alpha[i + 1] = np.clip(alpha[i + 1], -0.10, 0.30)  # safety bounds

    # ── HW1F bond price formula ──
    # P(t,T | r_t) = A(t,T) * exp(-B(t,T) * r_t)
    # B(t,T) = (1 - exp(-a*(T-t))) / a
    # A(t,T) = P(0,T)/P(0,t) * exp(B*f(0,t) - σ²/(4a)*B²*(1-exp(-2at)))

    def _B(t, T):
        tau = T - t
        if abs(a) < 1e-10:
            return tau
        return (1 - np.exp(-a * tau)) / a

    def _bond_price(t_years, T_years, r_t):
        """Price of a ZC bond maturing at T, given short rate r at time t."""
        with np.errstate(invalid='ignore', divide='ignore'):
            if T_years <= t_years:
                return 1.0
            B = _B(t_years, T_years)
            P0T = df_cdf_func(T_years * 365)
            P0t = df_cdf_func(t_years * 365)
            if P0t < 1e-15:
                return np.exp(-r_t * (T_years - t_years))
            f0t = _inst_fwd(t_years)
            lnA = np.log(P0T / P0t) + B * f0t - (sigma**2 / (4 * a)) * B**2 * (1 - np.exp(-2 * a * t_years))
            result = np.exp(lnA - B * r_t)
            if not np.isfinite(result):
                return np.exp(-r_t * (T_years - t_years))
            return result

    # ── Fonction de payoff à l'exercice ──
    def _exercise_payoff(step, j):
        """
        Payoff d'exercice au nœud (step, j).
        
        FIX P-MODÉRÉ: calcule la VA exacte du différentiel de cashflows,
        pas l'approximation moneyness × durée.
        
        Payoff = notional - PV(cashflows_client | r_node)
        
        Si la banque prorogerait, elle paie le taux client au lieu du taux marché.
        La valeur de cette option = ce que la banque économise en prorogeant
        = PV(financement au marché) - PV(financement au taux client)
        = notional - PV(coupons_client + principal | r_node, spread_market)
        """
        ex_date = ex_steps.get(step)
        if ex_date is None:
            return 0.0

        strike = strikes.get(ex_date, 0)  # = client_rate en %
        t_ex = times[step] if step < len(times) else times[-1]
        residual_days = (final_maturity - ex_date).days
        if residual_days <= 0:
            return 0.0

        r_node = alpha[step] + j * dr
        residual_years = residual_days / 365.0

        # Add market spread to get funding short rate
        spread_mkt = spread_market.spread_at(residual_days)
        r_funding = r_node + spread_mkt / 100.0  # spread is in %, r is decimal

        # PV of client cashflows discounted at funding rate
        client_rate_dec = strike / 100.0  # strike = client_rate

        if cpg_type == "COUPON" and freq_per_year > 0:
            n_periods = max(1, int(residual_years * freq_per_year))
            period_dt = residual_years / n_periods
            coupon_per_period = client_rate_dec * period_dt

            pv_client = 0
            for k in range(1, n_periods + 1):
                T_k = t_ex + k * period_dt
                df_k = _bond_price(t_ex, T_k, r_node)
                # Adjust for spread: df_funding = df_ois * exp(-spread * tau)
                tau_k = k * period_dt
                df_spread = np.exp(-spread_mkt / 100.0 * tau_k)
                pv_client += notional * coupon_per_period * df_k * df_spread

            # Principal at maturity
            T_mat = t_ex + residual_years
            df_mat = _bond_price(t_ex, T_mat, r_node)
            df_spread_mat = np.exp(-spread_mkt / 100.0 * residual_years)
            pv_client += notional * df_mat * df_spread_mat

        elif cpg_type == "LINEAR ACCRUAL":
            # Total payout = notional * (1 + rate * years) at maturity
            total_payout = notional * (1 + client_rate_dec * residual_years)
            T_mat = t_ex + residual_years
            df_mat = _bond_price(t_ex, T_mat, r_node)
            df_spread_mat = np.exp(-spread_mkt / 100.0 * residual_years)
            pv_client = total_payout * df_mat * df_spread_mat
        else:
            pv_client = notional * np.exp(-r_funding * residual_years)

        # Payoff = what we'd pay at market (par = notional) minus what we actually pay (client CFs)
        # If pv_client < notional → the bank is financing below market → positive payoff
        payoff = max(0, notional - pv_client)
        return payoff

    # ── Backward induction ──
    # Terminal values
    V = [np.zeros(n_nodes) for _ in range(N + 1)]

    # At last step: exercise if possible
    if N in ex_steps:
        for jj in range(-j_max, j_max + 1):
            V[N][jj + j_max] = max(0, _exercise_payoff(N, jj))

    # Roll back
    for i in range(N - 1, -1, -1):
        for jj in range(-j_max, j_max + 1):
            j_idx = jj + j_max
            r_ij = alpha[i] + jj * dr
            discount = np.exp(-r_ij * dt)
            pu, pm, pd = _probs(jj)

            # Continuation value
            cont = 0
            for dj, p in [(1, pu), (0, pm), (-1, pd)]:
                jnew = max(-j_max, min(j_max, jj + dj))
                val = V[i + 1][jnew + j_max]
                if np.isfinite(val):
                    cont += p * val
            cont *= discount
            if not np.isfinite(cont):
                cont = 0

            # Exercise value (only at exercise dates)
            if i in ex_steps:
                ex_val = _exercise_payoff(i, jj)
                V[i][j_idx] = max(ex_val, cont)
            else:
                V[i][j_idx] = cont

    bermudan_value = V[0][j_max]  # value at root node
    if not np.isfinite(bermudan_value):
        bermudan_value = best_intrinsic if best_intrinsic > 0 else 0.0
        log.warning(f"HW1F tree produced NaN, falling back to intrinsic={bermudan_value}")

    # ── Also compute max intrinsic for comparison ──
    best_intrinsic = 0
    exercise_analysis = []

    for step, ex_date in sorted(ex_steps.items()):
        payoff_central = _exercise_payoff(step, 0)  # j=0 = central node

        strike = strikes.get(ex_date, 0)
        residual_days = (final_maturity - ex_date).days
        days_to_ex = (ex_date - eval_date).days
        t_ex = times[step] if step < len(times) else times[-1]
        residual_years = residual_days / 365.0

        # OIS forward for display
        df_ex_ois = df_ois_func(days_to_ex)
        df_end_ois = df_ois_func(days_to_ex + residual_days)
        if df_end_ois > 1e-15 and residual_years > 0:
            ois_fwd = -(np.log(df_end_ois / df_ex_ois)) / residual_years * 100.0
        else:
            ois_fwd = alpha[step] * 100.0

        spread_mkt = spread_market.spread_at(residual_days)
        funding_fwd = ois_fwd + spread_mkt
        moneyness = funding_fwd - strike

        # PV of intrinsic back to today
        intrinsic_pv = payoff_central * df_cdf_func(days_to_ex)
        if not np.isfinite(intrinsic_pv):
            intrinsic_pv = 0.0
        if intrinsic_pv > best_intrinsic:
            best_intrinsic = intrinsic_pv

        exercise_analysis.append({
            "exercise_date": ex_date.strftime("%Y-%m-%d"),
            "residual_days": residual_days,
            "strike_pct": round(strike, 4),
            "ois_forward_pct": round(ois_fwd, 4),
            "spread_market_pct": round(spread_mkt, 4),
            "funding_forward_pct": round(funding_fwd, 4),
            "moneyness_bp": round(moneyness * 100, 1),
            "intrinsic_pv": round(intrinsic_pv, 2),
            "in_the_money": bool(moneyness > 0),
        })

    time_value = max(0, bermudan_value - best_intrinsic)

    return {
        "option_value": round(bermudan_value, 2),
        "intrinsic_value": round(best_intrinsic, 2),
        "time_value": round(time_value, 2),
        "method": f"HW1F trinomial (a={a}, σ={sigma_bp:.0f}pb, {N} pas)",
        "sigma_bp": sigma_bp,
        "mean_reversion": a,
        "n_steps": N,
        "exercise_analysis": exercise_analysis,
    }


def compute_vega_hw1f(
    df_cdf_func, df_ois_func, spread_market, eval_date,
    exercise_dates, strikes, notional, client_rate, cpg_type,
    freq_per_year, final_maturity,
    sigma_bp=65.0, a=0.03, bump_bp=1.0,
) -> Dict[str, Any]:
    """
    Véga par bump de σ dans l'arbre HW1F.
    Retourne la sensibilité de l'option à un choc de 1pb de vol.
    """
    v_base = price_bermudan_hw1f(
        df_cdf_func, df_ois_func, spread_market, eval_date,
        exercise_dates, strikes, notional, client_rate, cpg_type,
        freq_per_year, final_maturity, sigma_bp=sigma_bp, a=a,
    )["option_value"]

    v_up = price_bermudan_hw1f(
        df_cdf_func, df_ois_func, spread_market, eval_date,
        exercise_dates, strikes, notional, client_rate, cpg_type,
        freq_per_year, final_maturity, sigma_bp=sigma_bp + bump_bp, a=a,
    )["option_value"]

    vega = (v_up - v_base) / bump_bp

    return {
        "vega_1bp": round(vega, 4),
        "option_base": round(v_base, 2),
        "option_bumped": round(v_up, 2),
        "sigma_bp": sigma_bp,
        "bump_bp": bump_bp,
        "source": "HW1F tree bump",
    }

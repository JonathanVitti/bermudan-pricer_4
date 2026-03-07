#!/usr/bin/env python3
"""
tests/test_quant_invariants.py — Non-regression + quant invariants.

Tests that should ALWAYS pass:
  1. Golden regression (CPG portfolio PV matches snapshot)
  2. DV01 stability across bump sizes (1bp vs 0.5bp)
  3. CS01 ≈ DV01 for fixed cashflows (no optionality)
  4. Gamma consistency: same result whether gamma_bp == dv01_bp or not
  5. Bump symmetry: OIS bump preserves spread
"""
import json
import os
import sys
import math
import pytest
import pandas as pd
import numpy as np

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

ROOT = os.path.join(os.path.dirname(__file__), "..")
GOLDEN = os.path.join(os.path.dirname(__file__), "golden", "cpg_portfolio_2026-02-26.json")


@pytest.fixture(scope="module")
def curve_df():
    from cpg.curve_sql import load_curve_from_csv
    return load_curve_from_csv(os.path.join(ROOT, "data", "curve_sample.csv"))


@pytest.fixture(scope="module")
def trades_df(curve_df):
    from cpg.trades import load_trades_file
    return load_trades_file(os.path.join(ROOT, "data", "trades_sample.csv"))


# ═══════════════════════════════════════════════════════════════════════════
#  1. GOLDEN REGRESSION
# ═══════════════════════════════════════════════════════════════════════════

class TestGoldenRegression:
    """Portfolio PV must match the committed golden snapshot.
    If pricing logic changes, update the golden file explicitly."""

    def test_pv_total_matches_golden(self, trades_df, curve_df):
        from cpg.pricing import price_cpg_portfolio
        res = price_cpg_portfolio(trades_df, curve_df, "2026-02-26")
        ok = res[res["Status"] == "OK"]

        with open(GOLDEN) as f:
            ref = json.load(f)

        assert len(ok) == ref["n"]
        assert abs(ok["PV"].sum() - ref["pv_total"]) < 1e-2, (
            f"PV total drift: {ok['PV'].sum()} vs golden {ref['pv_total']}"
        )

    def test_pv_per_trade_matches_golden(self, trades_df, curve_df):
        from cpg.pricing import price_cpg_portfolio
        res = price_cpg_portfolio(trades_df, curve_df, "2026-02-26")
        ok = res[res["Status"] == "OK"].sort_values(
            ["CodeTransaction", "DateEcheanceFinal"]
        ).reset_index(drop=True)

        with open(GOLDEN) as f:
            ref = json.load(f)

        for i, (actual, expected) in enumerate(zip(ok["PV"], ref["pv_by_trade"])):
            tol = max(1e-2, abs(expected) * 1e-4)
            assert abs(actual - expected) < tol, (
                f"Trade {i}: PV={actual} vs golden {expected}"
            )


# ═══════════════════════════════════════════════════════════════════════════
#  2. DV01 STABILITY ACROSS BUMP SIZES
# ═══════════════════════════════════════════════════════════════════════════

class TestDV01Stability:
    """DV01(1bp) should be close to DV01(0.5bp).
    Invariant: finite difference is stable for a DCF portfolio."""

    def test_dv01_stable_across_bumps(self, trades_df, curve_df):
        from cpg.pricing import price_cpg_portfolio, bump_curve_ois

        def pv(c):
            r = price_cpg_portfolio(trades_df, c, "2026-02-26", component="cdf")
            return r.loc[r["Status"] == "OK", "PV"].sum()

        pv_base = pv(curve_df)

        # DV01 with 1bp bump
        pv_u1 = pv(bump_curve_ois(curve_df, +1.0))
        pv_d1 = pv(bump_curve_ois(curve_df, -1.0))
        dv01_1 = (pv_d1 - pv_u1) / 2.0

        # DV01 with 0.5bp bump
        pv_u05 = pv(bump_curve_ois(curve_df, +0.5))
        pv_d05 = pv(bump_curve_ois(curve_df, -0.5))
        dv01_05 = (pv_d05 - pv_u05) / 1.0  # / (2 * 0.5)

        # Should be within 5% of each other
        rel_diff = abs(dv01_1 - dv01_05) / max(abs(dv01_1), 1e-10)
        assert rel_diff < 0.05, (
            f"DV01 unstable: 1bp={dv01_1:.4f}, 0.5bp={dv01_05:.4f}, "
            f"rel_diff={rel_diff:.4f}"
        )


# ═══════════════════════════════════════════════════════════════════════════
#  3. CS01 ≈ DV01 FOR FIXED CASHFLOWS
# ═══════════════════════════════════════════════════════════════════════════

class TestCS01Invariant:
    """For a portfolio of fixed cashflows (no optionality),
    bumping OIS by 1bp has the same DF effect as bumping spread by 1bp.
    Therefore CS01 ≈ DV01."""

    def test_cs01_equals_dv01_for_fixed_cf(self, trades_df, curve_df):
        from cpg.greeks import compute_dv01
        from cpg.extendible import compute_cs01

        dv01 = compute_dv01(trades_df, curve_df, "2026-02-26", bump_bp=1.0)
        cs01 = compute_cs01(trades_df, curve_df, "2026-02-26", bump_bp=1.0)

        assert abs(dv01["DV01"] - cs01["CS01"]) < 0.01, (
            f"CS01 ({cs01['CS01']}) should equal DV01 ({dv01['DV01']}) "
            f"for fixed cashflows"
        )


# ═══════════════════════════════════════════════════════════════════════════
#  4. BUMP OIS PRESERVES SPREAD
# ═══════════════════════════════════════════════════════════════════════════

class TestBumpIntegrity:
    """OIS bump must not touch the spread component."""

    def test_ois_bump_preserves_spread(self, curve_df):
        from cpg.pricing import bump_curve_ois, has_curve_decomposition

        if not has_curve_decomposition(curve_df):
            pytest.skip("No OIS/Spread decomposition in test curve")

        bumped = bump_curve_ois(curve_df, +5.0)

        spread_before = curve_df["ZeroCouponSpreadCDF"].values
        spread_after = bumped["ZeroCouponSpreadCDF"].values
        np.testing.assert_array_almost_equal(
            spread_before, spread_after, decimal=10,
            err_msg="OIS bump must not change ZeroCouponSpreadCDF"
        )

    def test_ois_bump_moves_cdf_by_same_delta(self, curve_df):
        from cpg.pricing import bump_curve_ois

        bump = 3.0  # 3bp
        bumped = bump_curve_ois(curve_df, bump)

        delta_cdf = bumped["TauxCDF"].values - curve_df["TauxCDF"].values
        expected = bump / 100.0  # bp to %

        np.testing.assert_array_almost_equal(
            delta_cdf, np.full_like(delta_cdf, expected), decimal=10,
            err_msg="TauxCDF should move by exactly the OIS bump"
        )

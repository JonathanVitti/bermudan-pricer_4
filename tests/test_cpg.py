#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_cpg.py — Tests unitaires pour le module CPG.

Usage: python -m pytest tests/test_cpg.py -v
"""
import os, sys
import pytest
from datetime import datetime

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ═══════════════════════════════════════════════════════════════════════════
# Test 1: Parsing & standardization
# ═══════════════════════════════════════════════════════════════════════════

class TestTradesParsing:
    """Tests for column normalization and validation."""

    def test_column_normalization(self):
        from cpg.trades import standardize_trades_df
        raw = pd.DataFrame({
            "Code Transaction": ["COUPON"],
            "Date Émission": ["2025-12-19"],
            "Date Échéance Final": ["2035-12-19"],
            "Montant": ["1,000.00 $"],
            "Coupon": ["5.00%"],
            "Marge": ["0.00%"],
            "Fréquence": ["Annuel"],
            "Base Calcul": ["ACT/365"],
            "Devise": ["CAD"],
        })
        df = standardize_trades_df(raw)
        assert "CodeTransaction" in df.columns
        assert "DateEmission" in df.columns
        assert df.iloc[0]["Montant"] == 1000.0
        assert df.iloc[0]["Coupon"] == 5.0

    def test_missing_required_col(self):
        from cpg.trades import standardize_trades_df
        raw = pd.DataFrame({
            "CodeTransaction": ["COUPON"],
            "Montant": [1000],
            # Missing DateEcheanceFinal, Coupon, etc.
        })
        with pytest.raises(ValueError, match="Colonnes obligatoires manquantes"):
            standardize_trades_df(raw)

    def test_unsupported_currency(self):
        from cpg.trades import standardize_trades_df
        raw = pd.DataFrame({
            "CodeTransaction": ["COUPON"],
            "DateEmission": ["2025-12-19"],
            "DateEcheanceFinal": ["2035-12-19"],
            "Montant": [1000],
            "Coupon": [5.0],
            "Frequence": ["Annuel"],
            "BaseCalcul": ["ACT/365"],
            "Devise": ["USD"],  # Not CAD!
        })
        with pytest.raises(ValueError, match="Devise ≠ CAD"):
            standardize_trades_df(raw)

    def test_load_sample_file(self):
        from cpg.trades import load_trades_file
        path = os.path.join(os.path.dirname(__file__), "..", "data", "trades_sample.csv")
        if not os.path.exists(path):
            pytest.skip("Sample file not found")
        df = load_trades_file(path)
        assert len(df) == 6
        assert set(df["CodeTransaction"]) == {"COUPON", "LINEAR ACCRUAL"}


# ═══════════════════════════════════════════════════════════════════════════
# Test 2: Pricing monotonicity & sanity
# ═══════════════════════════════════════════════════════════════════════════

class TestPricingMonotonicity:
    """PV should decrease with higher discount rates (all else equal)."""

    def _make_flat_curve(self, rate_pct: float) -> pd.DataFrame:
        """Create a flat CDF curve at a given rate."""
        days = [1, 30, 90, 180, 365, 730, 1095, 1825, 2555, 3650, 7300, 10950]
        return pd.DataFrame({
            "ApproxDays": days,
            "TauxCDF": [rate_pct] * len(days),
        })

    def test_pv_decreases_with_higher_rate(self):
        from cpg.pricing import build_discount_function, price_coupon_bond

        pvs = []
        for rate in [2.0, 3.0, 4.0, 5.0, 6.0]:
            curve = self._make_flat_curve(rate)
            df_func = build_discount_function(curve)
            res = price_coupon_bond(
                notional=1000, coupon_rate=5.0, margin=0,
                emission=datetime(2025, 12, 19),
                first_coupon=datetime(2026, 12, 19),
                maturity=datetime(2035, 12, 19),
                eval_date=datetime(2026, 2, 26),
                freq_per_year=1,
                df_func=df_func,
            )
            pvs.append(res["PV"])

        # PV should be strictly decreasing as rate increases
        for i in range(len(pvs) - 1):
            assert pvs[i] > pvs[i + 1], f"PV not decreasing: rate {2+i}% -> {pvs[i]}, rate {3+i}% -> {pvs[i+1]}"

    def test_linear_accrual_sanity(self):
        from cpg.pricing import build_discount_function, price_linear_accrual

        curve = self._make_flat_curve(3.0)
        df_func = build_discount_function(curve)
        res = price_linear_accrual(
            notional=1000, coupon_rate=6.0, margin=0,
            emission=datetime(2025, 12, 19),
            maturity=datetime(2040, 12, 19),
            eval_date=datetime(2026, 2, 26),
            df_func=df_func,
        )
        # PV should be > 0
        assert res["PV"] > 0
        # PV_Principal + PV_Coupons should be close to PV
        # (for linear accrual they sum to PV since same DF)
        assert abs(res["PV"] - res["PV_Coupons"] - res["PV_Principal"]) < 0.01
        # Duration should be positive and roughly equal to time to maturity
        assert 10 < res["Duration_Approx"] < 16

    def test_matured_trade_returns_zero(self):
        from cpg.pricing import build_discount_function, price_single_cpg

        curve = self._make_flat_curve(3.0)
        df_func = build_discount_function(curve)

        row = pd.Series({
            "CodeTransaction": "COUPON",
            "DateEmission": datetime(2020, 1, 1),
            "DateEcheanceInitial": datetime(2021, 1, 1),
            "DateEcheanceFinal": datetime(2025, 1, 1),  # Before eval date
            "Montant": 1000,
            "Coupon": 5.0,
            "Marge": 0,
            "FreqPerYear": 1,
        })
        res = price_single_cpg(row, datetime(2026, 2, 26), df_func)
        assert res["PV"] == 0.0
        assert res["Status"] == "MATURED"


# ═══════════════════════════════════════════════════════════════════════════
# Test 3: Portfolio integration
# ═══════════════════════════════════════════════════════════════════════════

class TestPortfolio:
    """Integration test: load sample data, price, check output."""

    def test_full_pipeline(self):
        from cpg.trades import load_trades_file
        from cpg.curve_sql import load_curve_from_csv
        from cpg.pricing import price_cpg_portfolio

        data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
        trades_path = os.path.join(data_dir, "trades_sample.csv")
        curve_path = os.path.join(data_dir, "curve_sample.csv")

        if not os.path.exists(trades_path) or not os.path.exists(curve_path):
            pytest.skip("Sample data files not found")

        trades_df = load_trades_file(trades_path)
        curve_df = load_curve_from_csv(curve_path)
        results = price_cpg_portfolio(trades_df, curve_df, "2026-02-26")

        assert len(results) == 6
        assert (results["Status"] == "OK").all()
        assert (results["PV"] > 0).all()

        # Total PV should be reasonable (6 trades of 1000 notional)
        total_pv = results["PV"].sum()
        assert 3000 < total_pv < 12000, f"Total PV={total_pv} seems unreasonable"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

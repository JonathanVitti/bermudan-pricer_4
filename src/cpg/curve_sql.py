#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cpg/curve_sql.py — Récupération de la courbe de coût des fonds (CDF) via SQL.

Requête: Spread (CAD CDF) + Base (CAD OIS CORRA) = TauxCDF
Credentials: via variables d'environnement ou config.local.yaml (gitignored).

Usage:
    from cpg.curve_sql import fetch_funding_curve
    df = fetch_funding_curve("2026-02-26")
"""
import os, logging
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger("cpg.curve_sql")

# ─── Term ordering (pour tri déterministe) ────────────────────────────────
TERM_TYPE_ORDER = {"Day": 0, "Week": 1, "Month": 2, "Year": 3}
TERM_DAYS = {
    "Day": lambda p: p,
    "Week": lambda p: p * 7,
    "Month": lambda p: p * 30,  # approx — affiné par NbrJoursQRM si dispo
    "Year": lambda p: p * 365,
}

# ─── SQL template ─────────────────────────────────────────────────────────
SQL_CURVE = """
SELECT
    A.EvaluationDate,
    A.YieldCurve,
    A.termPoint,
    A.termType,
    A.ZeroCoupon  AS ZeroCouponSpreadCDF,
    B.ZeroCoupon  AS ZeroCouponBase,
    A.ZeroCoupon + B.ZeroCoupon AS TauxCDF
FROM [{schema}].[dbo].[{table}] AS A
LEFT JOIN [{schema}].[dbo].[{table}] AS B
    ON  B.CurveLabel = ?
    AND B.termPoint   = A.termPoint
    AND B.termType    = A.termType
    AND B.EvaluationDate = A.EvaluationDate
WHERE A.CurveLabel = ?
  AND A.EvaluationDate = ?
ORDER BY A.NbrJoursQRM
"""


def _get_connection_string() -> str:
    """Build ODBC connection string from env vars or config.local.yaml."""

    # Priority 1: env vars (recommended for production)
    conn = os.environ.get("CPG_SQL_CONN_STRING")
    if conn:
        return conn

    # Priority 2: individual env vars
    server = os.environ.get("CPG_SQL_SERVER")
    db = os.environ.get("CPG_SQL_DATABASE", "BD_ET_QRM_Staging")
    if server:
        driver = os.environ.get("CPG_SQL_DRIVER", "ODBC Driver 17 for SQL Server")
        trusted = os.environ.get("CPG_SQL_TRUSTED", "yes")
        return f"DRIVER={{{driver}}};SERVER={server};DATABASE={db};Trusted_Connection={trusted};"

    # Priority 3: config.local.yaml
    config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "config.local.yaml")
    if os.path.exists(config_path):
        import yaml
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
        sql_cfg = cfg.get("sql", {})
        if "connection_string" in sql_cfg:
            return sql_cfg["connection_string"]
        server = sql_cfg.get("server", "")
        db = sql_cfg.get("database", "BD_ET_QRM_Staging")
        driver = sql_cfg.get("driver", "ODBC Driver 17 for SQL Server")
        trusted = sql_cfg.get("trusted_connection", "yes")
        return f"DRIVER={{{driver}}};SERVER={server};DATABASE={db};Trusted_Connection={trusted};"

    raise EnvironmentError(
        "Aucune configuration SQL trouvée.\n"
        "Options:\n"
        "  1. Variable d'environnement CPG_SQL_CONN_STRING\n"
        "  2. Variables CPG_SQL_SERVER + CPG_SQL_DATABASE\n"
        "  3. Fichier config/config.local.yaml avec section 'sql:'\n"
    )


def fetch_funding_curve(
    eval_date: str,
    curve_label_spread: str = "CAD CDF",
    curve_label_base: str = "CAD OIS CORRA",
    schema: str = "BD_ET_QRM_Staging",
    table: str = "QRM_MUREX_YIELD_CURVE_QUOT",
) -> pd.DataFrame:
    """
    Récupère la courbe de coût des fonds depuis SQL.

    Parameters
    ----------
    eval_date : str
        Date d'évaluation au format YYYY-MM-DD.
    curve_label_spread : str
        CurveLabel pour le spread CDF.
    curve_label_base : str
        CurveLabel pour la base OIS.
    schema, table : str
        Schéma et table SQL.

    Returns
    -------
    pd.DataFrame
        Colonnes: EvaluationDate, termPoint, termType, ZeroCouponSpreadCDF,
                  ZeroCouponBase, TauxCDF, ApproxDays
    """
    try:
        import pyodbc
    except ImportError:
        raise ImportError("pyodbc requis pour l'accès SQL. Installer: pip install pyodbc")

    conn_str = _get_connection_string()
    log.info(f"Connexion SQL: {conn_str[:40]}...")

    eval_dt = datetime.strptime(eval_date, "%Y-%m-%d")

    try:
        conn = pyodbc.connect(conn_str, timeout=15)
    except Exception as e:
        raise ConnectionError(f"Échec connexion SQL: {e}")

    query = SQL_CURVE.format(schema=schema, table=table)

    try:
        df = pd.read_sql(query, conn, params=[curve_label_base, curve_label_spread, eval_dt])
    finally:
        conn.close()

    if df.empty:
        raise ValueError(
            f"Aucun point de courbe trouvé pour EvaluationDate={eval_date}, "
            f"CurveLabel='{curve_label_spread}'"
        )

    # Validate: no NaN in TauxCDF (spread present but base missing)
    missing_base = df["ZeroCouponBase"].isna()
    if missing_base.any():
        bad = df.loc[missing_base, ["termPoint", "termType"]].to_string(index=False)
        raise ValueError(
            f"Points de courbe avec spread mais sans base OIS:\n{bad}\n"
            "Règle: aucun NaN autorisé dans TauxCDF."
        )

    # Add approximate days for interpolation
    df["ApproxDays"] = df.apply(
        lambda r: TERM_DAYS.get(r["termType"], lambda p: p * 30)(int(r["termPoint"])),
        axis=1
    )

    # Log summary
    log.info(
        f"Courbe CDF récupérée: {len(df)} points, "
        f"plage [{df['ApproxDays'].min()}d – {df['ApproxDays'].max()}d], "
        f"eval_date={eval_date}"
    )

    return df


def load_curve_from_csv(path: str) -> pd.DataFrame:
    """
    Alternative: charger la courbe CDF depuis un fichier CSV.
    Format attendu: EvaluationDate, termPoint, termType, ZeroCouponSpreadCDF,
                    ZeroCouponBase, TauxCDF
    """
    df = pd.read_csv(path)
    required = {"termPoint", "termType", "TauxCDF"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Colonnes manquantes dans le CSV courbe: {missing}")

    if "ApproxDays" not in df.columns:
        df["ApproxDays"] = df.apply(
            lambda r: TERM_DAYS.get(r["termType"], lambda p: p * 30)(int(r["termPoint"])),
            axis=1
        )

    df = df.sort_values("ApproxDays").reset_index(drop=True)
    log.info(f"Courbe chargée depuis {path}: {len(df)} points")
    return df

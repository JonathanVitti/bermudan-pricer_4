#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cpg/providers.py — Architecture provider-based pour données de marché.

Le pricer ne sait jamais d'où viennent les données. Il consomme des objets
normalisés (DataFrames avec colonnes standard) via des providers.

Providers implémentés:
  - FileProvider    : CSV/Excel (mode offline, toujours disponible)
  - SQLProvider     : QRM Staging via SQLAlchemy (réseau Desjardins)
  - BloombergProvider : stub prêt pour branchement API blpapi

Usage:
    provider = get_provider("sql", config)      # ou "file" ou "bloomberg"
    curve_df = provider.fetch_curve("2026-02-26")
    vol_df   = provider.fetch_vol_surface("2026-02-26")

Config YAML:
    market_data:
      source: sql          # sql / file / bloomberg
      sql:
        server: MSSQL-DOT.Desjardins.com
        database: BD_ET_QRM_Staging
      file:
        curve_path: data/curve_sample.csv
        vol_path: data/vol_surface.csv
      bloomberg:
        curve_ticker: S490    # CAD OIS CORRA
        vol_ticker: VCUB      # CAD swaption vol cube
"""
import logging
import os
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

import pandas as pd
import numpy as np

log = logging.getLogger("cpg.providers")


# ═══════════════════════════════════════════════════════════════════════════
#  INTERFACE ABSTRAITE — tout provider implémente ces méthodes
# ═══════════════════════════════════════════════════════════════════════════

class MarketDataProvider(ABC):
    """
    Interface pour les sources de données de marché.

    Tout provider retourne des DataFrames avec des colonnes standard:
    - Courbe: ApproxDays, TauxCDF, ZeroCouponBase, ZeroCouponSpreadCDF
    - Vol: expiry_years, tenor_years, normal_vol_bp
    """

    @abstractmethod
    def fetch_curve(self, eval_date: str, **kwargs) -> pd.DataFrame:
        """
        Récupère la courbe de taux.

        Returns
        -------
        pd.DataFrame
            Colonnes: termPoint, termType, ZeroCouponSpreadCDF,
                      ZeroCouponBase, TauxCDF, ApproxDays
        """
        ...

    @abstractmethod
    def fetch_vol_surface(self, eval_date: str, **kwargs) -> Optional[Dict[str, Any]]:
        """
        Récupère la surface de volatilité.

        Returns
        -------
        dict or None
            {"expiry_grid": [...], "tenor_grid": [...],
             "vol_matrix": [[...]], "source": "..."}
        """
        ...

    @abstractmethod
    def name(self) -> str:
        """Nom du provider pour le logging."""
        ...

    def health_check(self) -> bool:
        """Vérifie si le provider est disponible."""
        return True


# ═══════════════════════════════════════════════════════════════════════════
#  FILE PROVIDER — CSV/Excel (toujours disponible, mode offline)
# ═══════════════════════════════════════════════════════════════════════════

class FileProvider(MarketDataProvider):
    """
    Charge les données depuis des fichiers locaux (CSV/Excel).
    Toujours disponible — aucune dépendance réseau.
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.curve_path = self.config.get("curve_path")
        self.vol_path = self.config.get("vol_path")

    def name(self) -> str:
        return "file"

    def fetch_curve(self, eval_date: str, path: str = None, **kwargs) -> pd.DataFrame:
        p = path or self.curve_path
        if not p or not os.path.exists(p):
            raise FileNotFoundError(f"Fichier courbe introuvable: {p}")

        from cpg.curve_sql import load_curve_from_csv
        df = load_curve_from_csv(p)
        log.info(f"FileProvider: courbe chargée depuis {p} ({len(df)} points)")
        return df

    def fetch_vol_surface(self, eval_date: str, path: str = None, **kwargs) -> Optional[Dict]:
        p = path or self.vol_path
        if not p or not os.path.exists(p):
            return None

        ext = os.path.splitext(p)[1].lower()
        if ext == ".csv":
            df = pd.read_csv(p)
        elif ext in (".xlsx", ".xls"):
            df = pd.read_excel(p)
        else:
            return None

        # Assume matrix format: first col = expiry, rest = tenor columns
        expiry_grid = df.iloc[:, 0].values.astype(float)
        tenor_cols = [float(c) for c in df.columns[1:]]
        vol_matrix = df.iloc[:, 1:].values.astype(float).tolist()

        return {
            "expiry_grid": expiry_grid.tolist(),
            "tenor_grid": tenor_cols,
            "vol_matrix": vol_matrix,
            "source": f"file:{os.path.basename(p)}",
        }


# ═══════════════════════════════════════════════════════════════════════════
#  SQL PROVIDER — QRM Staging (réseau Desjardins)
# ═══════════════════════════════════════════════════════════════════════════

class SQLProvider(MarketDataProvider):
    """
    Récupère les données depuis QRM_STAGING via SQLAlchemy + pyodbc.
    Nécessite: SQLAlchemy, pyodbc, accès réseau SQL Server.
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.server = self.config.get("server", "MSSQL-DOT.Desjardins.com")
        self.database = self.config.get("database", "BD_ET_QRM_Staging")
        self.driver = self.config.get("driver", "ODBC Driver 17 for SQL Server")
        self._engine = None

    def name(self) -> str:
        return "sql"

    def _get_engine(self):
        if self._engine is None:
            from sqlalchemy import create_engine
            url = (
                f"mssql+pyodbc://@{self.server}/{self.database}"
                f"?driver={self.driver.replace(' ', '+')}&trusted_connection=yes"
            )
            self._engine = create_engine(url, pool_pre_ping=True, pool_size=3)
        return self._engine

    def health_check(self) -> bool:
        try:
            from sqlalchemy import text
            with self._get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            log.warning(f"SQLProvider health check failed: {e}")
            return False

    def fetch_curve(self, eval_date: str, **kwargs) -> pd.DataFrame:
        from sqlalchemy import text
        engine = self._get_engine()

        sql = text("""
            WITH latest AS (
                SELECT MAX(EvaluationDate) AS EvaluationDate
                FROM [BD_ET_QRM_Staging].[dbo].[QRM_MUREX_YIELD_CURVE_QUOT]
                WHERE EvaluationDate <= :cutoff
                  AND CurveLabel IN ('CAD CDF','CAD OIS CORRA')
            )
            SELECT A.EvaluationDate, A.termPoint, A.termType,
                   A.ZeroCoupon AS ZeroCouponSpreadCDF,
                   B.ZeroCoupon AS ZeroCouponBase,
                   (A.ZeroCoupon + B.ZeroCoupon) AS TauxCDF,
                   A.NbrJoursQRM AS ApproxDays
            FROM [BD_ET_QRM_Staging].[dbo].[QRM_MUREX_YIELD_CURVE_QUOT] AS A
            LEFT JOIN [BD_ET_QRM_Staging].[dbo].[QRM_MUREX_YIELD_CURVE_QUOT] AS B
                ON B.CurveLabel = 'CAD OIS CORRA'
                AND B.termPoint = A.termPoint AND B.termType = A.termType
                AND B.EvaluationDate = (SELECT EvaluationDate FROM latest)
            WHERE A.CurveLabel = 'CAD CDF'
              AND A.EvaluationDate = (SELECT EvaluationDate FROM latest)
            ORDER BY A.NbrJoursQRM
        """)

        from datetime import datetime
        cutoff = datetime.strptime(eval_date, "%Y-%m-%d").date()

        with engine.begin() as conn:
            df = pd.read_sql_query(sql, conn, params={"cutoff": cutoff})

        if df.empty:
            raise ValueError(f"SQLProvider: aucune courbe pour eval_date <= {eval_date}")

        df["ApproxDays"] = df["ApproxDays"].astype(int)
        df = df.sort_values("ApproxDays").reset_index(drop=True)
        log.info(f"SQLProvider: courbe {len(df)} pts, eval={df['EvaluationDate'].iloc[0]}")
        return df

    def fetch_vol_surface(self, eval_date: str, **kwargs) -> Optional[Dict]:
        # QRM Staging n'a pas de table vol swaption standard
        # Retourne None → le pricer utilisera le proxy
        log.info("SQLProvider: pas de surface vol dans QRM Staging, utiliser proxy ou fichier")
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  BLOOMBERG PROVIDER — stub prêt pour branchement API
# ═══════════════════════════════════════════════════════════════════════════

class BloombergProvider(MarketDataProvider):
    """
    Stub pour l'intégration Bloomberg.

    Mêmes signatures, mêmes objets retournés que les autres providers.
    Quand l'API blpapi sera disponible, implémenter les méthodes marquées TODO.

    Config attendue:
        bloomberg:
          curve_ticker: "S490"          # CAD OIS CORRA swap curve
          spread_ticker: "C490"         # CAD CDF funding curve
          vol_ticker: "VCUB"            # Normal vol swaption cube
          override_fields:
            PX_LAST: "PX_LAST"
            SETTLE_DT: "SETTLE_DT"
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        self._check_blpapi()

    def name(self) -> str:
        return "bloomberg"

    def _check_blpapi(self):
        try:
            import blpapi  # noqa: F401
            self._available = True
        except ImportError:
            self._available = False
            log.warning(
                "BloombergProvider: blpapi non installé. "
                "Installer depuis Bloomberg Terminal ou utiliser un autre provider."
            )

    def health_check(self) -> bool:
        if not self._available:
            return False
        # TODO: ouvrir une session blpapi et vérifier la connexion
        # session = blpapi.Session()
        # return session.start()
        return False

    def fetch_curve(self, eval_date: str, **kwargs) -> pd.DataFrame:
        """
        TODO: Implémenter quand blpapi disponible.

        Pseudo-code:
            1. Ouvrir session blpapi
            2. Request ReferenceDataRequest sur curve_ticker
            3. Parser les discount factors / zero rates
            4. Construire le DataFrame standard
        """
        if not self._available:
            raise RuntimeError(
                "Bloomberg non disponible. "
                "Utiliser source: sql ou source: file dans la config."
            )

        # ── PLACEHOLDER — remplacer par appel blpapi réel ──
        raise NotImplementedError(
            "BloombergProvider.fetch_curve() pas encore implémenté. "
            "Voir les commentaires dans le code pour le pseudo-code."
        )

        # Quand implémenté, retourner un DataFrame avec ces colonnes:
        # return pd.DataFrame({
        #     "termPoint": [...],
        #     "termType": [...],
        #     "ZeroCouponSpreadCDF": [...],
        #     "ZeroCouponBase": [...],
        #     "TauxCDF": [...],
        #     "ApproxDays": [...],
        # })

    def fetch_vol_surface(self, eval_date: str, **kwargs) -> Optional[Dict]:
        """
        TODO: Implémenter quand blpapi disponible.

        Pseudo-code:
            1. Request BulkReferenceDataRequest sur vol_ticker
            2. Parser la matrice expiry × tenor
            3. Convertir en normal vol (bp)
            4. Retourner le dict standard
        """
        if not self._available:
            return None

        raise NotImplementedError(
            "BloombergProvider.fetch_vol_surface() pas encore implémenté."
        )


# ═══════════════════════════════════════════════════════════════════════════
#  FACTORY — sélection du provider par config
# ═══════════════════════════════════════════════════════════════════════════

_PROVIDERS = {
    "file": FileProvider,
    "csv": FileProvider,
    "sql": SQLProvider,
    "bloomberg": BloombergProvider,
    "bbg": BloombergProvider,
}


def get_provider(source: str, config: dict = None) -> MarketDataProvider:
    """
    Factory: retourne le bon provider selon la source.

    Parameters
    ----------
    source : str
        "file", "sql", "bloomberg" (ou alias "csv", "bbg")
    config : dict
        Configuration spécifique au provider.

    Usage:
        provider = get_provider("sql", {"server": "...", "database": "..."})
        curve = provider.fetch_curve("2026-02-26")
    """
    source = source.lower().strip()
    cls = _PROVIDERS.get(source)
    if cls is None:
        available = ", ".join(_PROVIDERS.keys())
        raise ValueError(f"Provider inconnu: '{source}'. Disponibles: {available}")

    provider = cls(config or {})
    log.info(f"Provider créé: {provider.name()} ({cls.__name__})")
    return provider


def get_provider_from_yaml(yaml_path: str = "config/config.yaml") -> MarketDataProvider:
    """
    Charge le provider depuis le fichier config YAML.

    Attend une section:
        market_data:
          source: sql
          sql:
            server: MSSQL-DOT.Desjardins.com
    """
    import yaml
    if not os.path.exists(yaml_path):
        log.warning(f"Config {yaml_path} introuvable, fallback FileProvider")
        return FileProvider()

    with open(yaml_path) as f:
        cfg = yaml.safe_load(f) or {}

    md = cfg.get("market_data", {})
    source = md.get("source", "file")
    provider_config = md.get(source, {})
    return get_provider(source, provider_config)

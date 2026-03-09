#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cpg/trades.py — Chargement et normalisation des transactions CPG.

Deux sources: fichier (CSV/Excel) ou SQL (stub prêt).
Sortie: DataFrame standardisé avec colonnes canoniques.
"""
import os, re, logging
from datetime import datetime
from typing import Optional, List

import pandas as pd
import numpy as np

log = logging.getLogger("cpg.trades")

# ─── Colonnes canoniques ──────────────────────────────────────────────────
CANONICAL_COLS = [
    "CodeTransaction", "Inventaire", "Contrepartie",
    "DateEmission", "DateEcheanceInitial", "DateEcheanceFinal",
    "Montant", "Coupon", "Marge", "Frequence",
    "BaseCalcul", "Devise", "CUSIP", "FundServ",
]

# Mapping des variantes de noms de colonnes → nom canonique
_COL_ALIASES = {
    # CodeTransaction
    "codetransaction": "CodeTransaction",
    "code_transaction": "CodeTransaction",
    "type": "CodeTransaction",
    "code transaction": "CodeTransaction",
    # Inventaire
    "inventaire": "Inventaire",
    "inventory": "Inventaire",
    # Contrepartie
    "contrepartie": "Contrepartie",
    "counterparty": "Contrepartie",
    # Dates
    "dateemission": "DateEmission",
    "date_emission": "DateEmission",
    "dateémission": "DateEmission",
    "date émission": "DateEmission",
    "issue_date": "DateEmission",
    "issuedate": "DateEmission",
    "dateecheanceinitial": "DateEcheanceInitial",
    "date_echeance_initial": "DateEcheanceInitial",
    "dateéchéanceinitial": "DateEcheanceInitial",
    "date échéance initial": "DateEcheanceInitial",
    "first_maturity": "DateEcheanceInitial",
    "dateecheancefinal": "DateEcheanceFinal",
    "date_echeance_final": "DateEcheanceFinal",
    "dateéchéancefinal": "DateEcheanceFinal",
    "date échéance final": "DateEcheanceFinal",
    "maturity": "DateEcheanceFinal",
    "final_maturity": "DateEcheanceFinal",
    # Montant
    "montant": "Montant",
    "notional": "Montant",
    "principal": "Montant",
    "amount": "Montant",
    # Coupon
    "coupon": "Coupon",
    "rate": "Coupon",
    "taux": "Coupon",
    "coupon_rate": "Coupon",
    # Marge
    "marge": "Marge",
    "margin": "Marge",
    "spread": "Marge",
    # Fréquence
    "frequence": "Frequence",
    "fréquence": "Frequence",
    "frequency": "Frequence",
    "freq": "Frequence",
    # BaseCalcul
    "basecalcul": "BaseCalcul",
    "base_calcul": "BaseCalcul",
    "base calcul": "BaseCalcul",
    "daycount": "BaseCalcul",
    "day_count": "BaseCalcul",
    # Devise
    "devise": "Devise",
    "currency": "Devise",
    "ccy": "Devise",
    # CUSIP
    "cusip": "CUSIP",
    # FundServ
    "fundserv": "FundServ",
    "fund_serv": "FundServ",
}

REQUIRED_COLS = [
    "CodeTransaction", "DateEmission", "DateEcheanceFinal",
    "Montant", "Coupon", "Frequence", "BaseCalcul", "Devise",
]

SUPPORTED_TYPES = {"COUPON", "LINEAR ACCRUAL"}
FREQ_MAP = {
    "annuel": 1, "annual": 1, "1": 1,
    "semestriel": 2, "semiannual": 2, "semi-annual": 2, "2": 2,
    "trimestriel": 4, "quarterly": 4, "4": 4,
    "mensuel": 12, "monthly": 12, "12": 12,
    "maturité": 0, "maturity": 0, "maturite": 0, "à maturité": 0, "0": 0,
}


def _normalize_col_name(name: str) -> str:
    """Normalize column name: strip, lowercase, remove accents."""
    s = str(name).strip()
    # Remove common decorators
    s = re.sub(r"[\s_\-]+", "", s.lower())
    # Remove accents (simple)
    for a, b in [("é", "e"), ("è", "e"), ("ê", "e"), ("à", "a"), ("î", "i"), ("û", "u")]:
        s = s.replace(a, b)
    return s


def _map_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Map raw column names to canonical names."""
    mapped = {}
    for col in df.columns:
        norm = _normalize_col_name(col)
        if norm in _COL_ALIASES:
            mapped[col] = _COL_ALIASES[norm]
        else:
            # Try partial match
            for alias, canonical in _COL_ALIASES.items():
                if alias in norm or norm in alias:
                    mapped[col] = canonical
                    break
    df = df.rename(columns=mapped)
    return df


def _clean_pct(val) -> float:
    """Convert percentage string or number to float (as percent, e.g. 5.0 for 5%)."""
    if pd.isna(val):
        return 0.0
    if isinstance(val, str):
        val = val.strip().replace("%", "").replace(",", ".").replace(" ", "")
        try:
            return float(val)
        except ValueError:
            return 0.0
    return float(val)


def _clean_amount(val) -> float:
    """Clean monetary amount: remove $, spaces, thousands separators."""
    if pd.isna(val):
        return 0.0
    if isinstance(val, str):
        val = val.replace("$", "").replace(" ", "").replace("\xa0", "")
        val = re.sub(r"(?<=\d),(?=\d{3})", "", val)  # remove thousands comma
        val = val.replace(",", ".")  # decimal comma to dot
        try:
            return float(val)
        except ValueError:
            return 0.0
    return float(val)


def _parse_date(val) -> Optional[datetime]:
    """Parse date from various formats."""
    if pd.isna(val):
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, pd.Timestamp):
        return val.to_pydatetime()
    s = str(val).strip().split()[0]  # drop time part
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def standardize_trades_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize a raw trades DataFrame to canonical format.
    Returns standardized DataFrame with proper types, validated.
    """
    df = _map_columns(df.copy())

    # 1) Colonnes obligatoires
    present = set(df.columns)
    missing = [c for c in REQUIRED_COLS if c not in present]
    if missing:
        raise ValueError(
            f"Colonnes obligatoires manquantes: {missing}\n"
            f"Colonnes trouvées: {sorted(present)}"
        )

    # 2) Nettoyage précoce de quelques colonnes texte (évite .str sur NaN)
    for c in ("CodeTransaction", "Devise", "Frequence", "BaseCalcul",
              "CUSIP", "FundServ", "Inventaire", "Contrepartie"):
        if c in df.columns:
            df[c] = df[c].astype(str).strip() if isinstance(df[c], str) else df[c].astype(str).str.strip()

    # 3) Dates
    for dcol in ["DateEmission", "DateEcheanceInitial", "DateEcheanceFinal"]:
        if dcol in df.columns:
            df[dcol] = df[dcol].apply(_parse_date)

    # 4) Numériques / formats locaux
    df["Montant"] = df["Montant"].apply(_clean_amount)
    df["Coupon"]  = df["Coupon"].apply(_clean_pct)  # 5.00% -> 5.00
    df["Marge"]   = df.get("Marge", pd.Series(0.0, index=df.index)).apply(_clean_pct)

    # 5) Normalise "Devise"
    df["Devise"] = df["Devise"].astype(str).str.strip().str.upper()
    df["Devise"] = df["Devise"].replace({"": "CAD", "NAN": "CAD"})

    # 6) Validations
    errors = []

    # Devise
    non_cad = df[df["Devise"].str.upper() != "CAD"]
    if not non_cad.empty:
        errors.append(f"Devise ≠ CAD détectée pour {len(non_cad)} lignes. Seul CAD supporté.")

    # Type de transaction
    df["CodeTransaction"] = df["CodeTransaction"].astype(str).str.upper().str.strip()
    unsupported = set(df["CodeTransaction"]) - SUPPORTED_TYPES
    if unsupported:
        errors.append(f"Types de transaction non supportés: {unsupported}. Supportés: {SUPPORTED_TYPES}")

    # Montants
    if (df["Montant"] <= 0).any():
        errors.append("Montant ≤ 0 détecté pour certaines lignes.")

    # Dates
    null_dates = df["DateEcheanceFinal"].isna()
    if null_dates.any():
        errors.append(f"{null_dates.sum()} lignes sans DateEcheanceFinal valide.")

    if errors:
        raise ValueError("Erreurs de validation des trades:\n  • " + "\n  • ".join(errors))

    # 7) Fréquence -> FreqPerYear
    df["FreqPerYear"] = df["Frequence"].astype(str).str.strip().str.lower().map(FREQ_MAP)
    unmapped = df["FreqPerYear"].isna()
    if unmapped.any():
        bad = df.loc[unmapped, "Frequence"].unique()
        raise ValueError(f"Fréquences non reconnues: {list(bad)}. Valeurs acceptées: {list(FREQ_MAP.keys())}")
    df["FreqPerYear"] = df["FreqPerYear"].astype(int)

    # 8) Remplit les colonnes optionnelles manquantes
    for col in CANONICAL_COLS:
        if col not in df.columns:
            df[col] = ""

    # 9) Nettoyage final des colonnes texte usuelles (INDENTATION FIX ICI)
    for c in ("CUSIP", "FundServ", "Inventaire", "Contrepartie", "BaseCalcul", "Frequence"):
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()

    log.info(f"Trades standardisés: {len(df)} lignes, types={df['CodeTransaction'].value_counts().to_dict()}")
    return df




def load_trades_file(path: str) -> pd.DataFrame:
    """
    Charge un fichier de transactions (CSV ou Excel), puis standardise le DataFrame.
    - Excel: utilise openpyxl
    - CSV: essaie plusieurs encodages (utf-8-sig, utf-8, cp1252) et séparateurs (',', ';', '\\t')
      avec une heuristique simple: un CSV valide a > 3 colonnes.
    """
    ext = os.path.splitext(path)[1].lower()

    if ext in (".xlsx", ".xls"):
        raw = pd.read_excel(path, engine="openpyxl")

    elif ext == ".csv":
        # Essais encodage/séparateur robustes
        last_err = None
        success = False
        for enc in ("utf-8-sig", "utf-8", "cp1252"):
            for sep in (",", ";", "\t"):
                try:
                    raw = pd.read_csv(path, sep=sep, encoding=enc)
                    # Heuristique: un CSV valide aura > 3 colonnes
                    if raw.shape[1] > 3:
                        success = True
                        break
                except Exception as e:
                    last_err = e
                    continue
            if success:
                break

        if not success:
            # Dernier essai "par défaut"
            try:
                raw = pd.read_csv(path)
                success = True
            except Exception as e:
                # On renvoie la dernière erreur rencontrée si disponible
                raise last_err or e

    else:
        raise ValueError(f"Format non supporté: {ext}. Utiliser .csv, .xlsx ou .xls")

    log.info(f"Fichier chargé: {path} ({len(raw)} lignes, {len(raw.columns)} colonnes)")
    return standardize_trades_df(raw)



def fetch_cpg_trades(
    eval_date: str,
    filters: Optional[dict] = None,
) -> pd.DataFrame:
    """
    Stub: extraction SQL des transactions CPG.
    À implémenter quand la requête SQL sera fournie.

    Retourne le même format que load_trades_file().
    """
    raise NotImplementedError(
        "fetch_cpg_trades() n'est pas encore implémenté.\n"
        "Utiliser load_trades_file() pour charger depuis un fichier."
    )

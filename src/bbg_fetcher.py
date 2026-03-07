#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
bbg_fetcher.py — Bloomberg Data Fetcher for Bermudan Swaption Pricer
=====================================================================
Two modes:
  1. "bloomberg" — fetches live data via blpapi (requires Bloomberg Terminal)
  2. "manual"    — reads from CSV files or inline YAML data

Returns standardized data dict:
  {
    "curve":       [(date_str, df), ...],
    "vol_surface": np.ndarray (BPx10 scale),
    "expiry_grid": [...],
    "tenor_grid":  [...],
    "bbg_npv":     float,
  }
"""

import os
import numpy as np
from datetime import datetime

# ═══════════════════════════════════════════════════════════════════════════
#  EXPIRY / TENOR GRID MAPPINGS
# ═══════════════════════════════════════════════════════════════════════════

EXPIRY_LABEL_TO_YEARS = {
    "1Mo": 1/12, "2Mo": 2/12, "3Mo": 3/12, "6Mo": 6/12, "9Mo": 9/12,
    "1Yr": 1, "2Yr": 2, "3Yr": 3, "4Yr": 4, "5Yr": 5,
    "6Yr": 6, "7Yr": 7, "8Yr": 8, "9Yr": 9, "10Yr": 10,
    "12Yr": 12, "15Yr": 15, "20Yr": 20, "25Yr": 25, "30Yr": 30,
}

TENOR_LABEL_TO_YEARS = {
    "1Y": 1, "2Y": 2, "3Y": 3, "4Y": 4, "5Y": 5,
    "6Y": 6, "7Y": 7, "8Y": 8, "9Y": 9, "10Y": 10,
    "12Y": 12, "15Y": 15, "20Y": 20, "25Y": 25, "30Y": 30,
}


def labels_to_years(labels, mapping):
    return np.array([mapping[l] for l in labels], dtype=float)


# ═══════════════════════════════════════════════════════════════════════════
#  BLOOMBERG MODE
# ═══════════════════════════════════════════════════════════════════════════

def _check_blpapi():
    try:
        import blpapi
        return True
    except ImportError:
        return False


def fetch_curve_bloomberg(cfg):
    """
    Fetch discount curve from Bloomberg via blpapi.
    Uses ICVS curve (default: YCSW0147 = CAD OIS).
    
    Returns: list of (date_str, discount_factor)
    """
    import blpapi

    bbg_cfg = cfg["data_source"]["bloomberg"]
    curve_ticker = bbg_cfg.get("curve_ticker", "YCSW0147 Index")
    val_date = cfg["deal"]["valuation_date"]

    sessionOptions = blpapi.SessionOptions()
    sessionOptions.setServerHost("localhost")
    sessionOptions.setServerPort(8194)

    session = blpapi.Session(sessionOptions)
    if not session.start():
        raise RuntimeError("Bloomberg session failed to start")
    if not session.openService("//blp/refdata"):
        raise RuntimeError("Failed to open //blp/refdata")

    refDataService = session.getService("//blp/refdata")
    request = refDataService.createRequest("ReferenceDataRequest")
    request.getElement("securities").appendValue(curve_ticker)

    # Request curve members with discount factors
    overrides = request.getElement("overrides")

    # Set curve date
    o1 = overrides.appendElement()
    o1.setElement("fieldId", "CURVE_DATE")
    o1.setElement("value", val_date)

    request.getElement("fields").appendValue("CURVE_TENOR_RATES")

    session.sendRequest(request)

    curve_data = []
    while True:
        ev = session.nextEvent(int(bbg_cfg.get("timeout_ms", 30000)))
        for msg in ev:
            if msg.hasElement("securityData"):
                sec = msg.getElement("securityData").getValueAsElement(0)
                if sec.hasElement("fieldData"):
                    fd = sec.getElement("fieldData")
                    if fd.hasElement("CURVE_TENOR_RATES"):
                        rates = fd.getElement("CURVE_TENOR_RATES")
                        for i in range(rates.numValues()):
                            point = rates.getValueAsElement(i)
                            tenor_date = str(point.getElementAsString("Tenor Date"))
                            df = float(point.getElementAsFloat("Discount Factor"))
                            curve_data.append((tenor_date, df))
        if ev.eventType() == blpapi.Event.RESPONSE:
            break

    session.stop()
    return curve_data


def fetch_vol_surface_bloomberg(cfg):
    """
    Fetch ATM normal vol surface from Bloomberg.
    Uses BVOL or VCUB.
    
    Returns: (np.ndarray BPx10, expiry_labels, tenor_labels)
    """
    import blpapi

    bbg_cfg = cfg["data_source"]["bloomberg"]

    # Build swaption vol tickers
    # BBG convention: CADSN{expiry}{tenor} Curncy for normal vols
    # Example: CADSN1Y5Y Curncy = CAD 1Y into 5Y normal vol
    expiry_labels = ["1Mo","3Mo","6Mo","9Mo","1Yr","2Yr","3Yr","4Yr","5Yr",
                     "6Yr","7Yr","8Yr","9Yr","10Yr","12Yr","15Yr","20Yr","25Yr"]
    tenor_labels  = ["1Y","2Y","3Y","4Y","5Y","6Y","7Y","8Y","9Y","10Y",
                     "12Y","15Y","20Y","25Y","30Y"]

    # Map labels to BBG ticker format
    expiry_bbg = {
        "1Mo":"1M","3Mo":"3M","6Mo":"6M","9Mo":"9M","1Yr":"1Y","2Yr":"2Y",
        "3Yr":"3Y","4Yr":"4Y","5Yr":"5Y","6Yr":"6Y","7Yr":"7Y","8Yr":"8Y",
        "9Yr":"9Y","10Yr":"10Y","12Yr":"12Y","15Yr":"15Y","20Yr":"20Y","25Yr":"25Y",
    }
    tenor_bbg = {
        "1Y":"1Y","2Y":"2Y","3Y":"3Y","4Y":"4Y","5Y":"5Y","6Y":"6Y",
        "7Y":"7Y","8Y":"8Y","9Y":"9Y","10Y":"10Y","12Y":"12Y","15Y":"15Y",
        "20Y":"20Y","25Y":"25Y","30Y":"30Y",
    }

    tickers = []
    for exp in expiry_labels:
        for tnr in tenor_labels:
            # Normal vol ticker: CADSN{exp}{tnr} Curncy
            t = f"CADSN{expiry_bbg[exp]}{tenor_bbg[tnr]} Curncy"
            tickers.append(t)

    sessionOptions = blpapi.SessionOptions()
    sessionOptions.setServerHost("localhost")
    sessionOptions.setServerPort(8194)

    session = blpapi.Session(sessionOptions)
    if not session.start():
        raise RuntimeError("Bloomberg session failed to start")
    if not session.openService("//blp/refdata"):
        raise RuntimeError("Failed to open //blp/refdata")

    refDataService = session.getService("//blp/refdata")

    # Batch request (BBG limit ~few hundred tickers per request)
    vol_dict = {}
    batch_size = 50
    for batch_start in range(0, len(tickers), batch_size):
        batch = tickers[batch_start:batch_start + batch_size]
        request = refDataService.createRequest("ReferenceDataRequest")
        for t in batch:
            request.getElement("securities").appendValue(t)
        request.getElement("fields").appendValue("PX_LAST")

        session.sendRequest(request)

        while True:
            ev = session.nextEvent(int(bbg_cfg.get("timeout_ms", 30000)))
            for msg in ev:
                if msg.hasElement("securityData"):
                    secs = msg.getElement("securityData")
                    for j in range(secs.numValues()):
                        sec = secs.getValueAsElement(j)
                        ticker = sec.getElementAsString("security")
                        if sec.hasElement("fieldData"):
                            fd = sec.getElement("fieldData")
                            if fd.hasElement("PX_LAST"):
                                val = fd.getElementAsFloat("PX_LAST")
                                vol_dict[ticker] = val
            if ev.eventType() == blpapi.Event.RESPONSE:
                break

    session.stop()

    # Build matrix
    vol_matrix = np.zeros((len(expiry_labels), len(tenor_labels)))
    for i, exp in enumerate(expiry_labels):
        for j, tnr in enumerate(tenor_labels):
            t = f"CADSN{expiry_bbg[exp]}{tenor_bbg[tnr]} Curncy"
            # BBG returns normal vols — need to figure out scale
            # Typically in bp or BPx10 depending on source
            val = vol_dict.get(t, np.nan)
            vol_matrix[i, j] = val

    # Detect scale: if max > 100, likely bp → convert to BPx10
    if np.nanmax(vol_matrix) > 100:
        vol_matrix = vol_matrix / 10.0  # bp → BPx10
    elif np.nanmax(vol_matrix) < 1:
        vol_matrix = vol_matrix * 1000.0  # decimal → BPx10

    return vol_matrix, expiry_labels, tenor_labels


def fetch_swaption_npv_bloomberg(cfg):
    """
    Fetch Bermudan swaption NPV from Bloomberg SWPM.
    This is optional — if not available, user enters NPV manually.
    
    In practice, SWPM NPV is not easily fetchable via standard blpapi.
    This function returns None and the NPV should be set in config.
    """
    # NOTE: Bloomberg SWPM valuations require the DLIB API or specific
    # override fields that are not universally available via blpapi.
    # The recommended workflow is:
    #   1. Price the deal in SWPM on the terminal
    #   2. Copy the NPV to config.yaml → benchmark.npv
    print("  [INFO] SWPM NPV cannot be fetched via standard blpapi.")
    print("  [INFO] Please enter the NPV in config.yaml → benchmark.npv")
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  MANUAL MODE
# ═══════════════════════════════════════════════════════════════════════════

def load_curve_csv(filepath):
    """Load curve from CSV: date,discount_factor"""
    data = []
    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("date"):
                continue
            parts = line.split(",")
            if len(parts) >= 2:
                data.append((parts[0].strip(), float(parts[1].strip())))
    return data


def load_vol_csv(filepath):
    """
    Load vol surface from CSV.
    First row: header with tenor labels (skip first cell)
    First col: expiry labels
    Values: BPx10
    """
    rows = []
    expiry_labels = []
    tenor_labels = []
    with open(filepath, "r") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(",")
            if i == 0 or (not rows and not parts[0].replace(".","").replace("-","").isdigit()):
                tenor_labels = [p.strip() for p in parts[1:]]
            else:
                expiry_labels.append(parts[0].strip())
                rows.append([float(x.strip()) for x in parts[1:]])
    return np.array(rows, dtype=float), expiry_labels, tenor_labels


def load_curve_yaml(cfg):
    """Load curve from inline YAML data."""
    return [(row[0], float(row[1])) for row in cfg.get("curve_data", [])]


def load_vol_yaml(cfg):
    """Load vol surface from inline YAML data."""
    vsd = cfg.get("vol_surface_data", {})
    expiry_labels = vsd.get("expiry_labels", [])
    tenor_labels  = vsd.get("tenor_labels", [])
    values = np.array(vsd.get("values", []), dtype=float)
    return values, expiry_labels, tenor_labels


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN DISPATCHER
# ═══════════════════════════════════════════════════════════════════════════

def fetch_all(cfg, config_dir="."):
    """
    Fetch all required data based on config.
    
    Returns dict:
      curve:       list of (date_str, df)
      vol_surface: np.ndarray (BPx10)
      expiry_grid: np.ndarray (years)
      tenor_grid:  np.ndarray (years)
      bbg_npv:     float (target NPV for inverse calibration)
    """
    mode = cfg.get("data_source", {}).get("mode", "manual")
    result = {}

    print(f"  Data source: {mode}")

    if mode == "bloomberg":
        if not _check_blpapi():
            print("  [WARNING] blpapi not installed — falling back to manual mode")
            print("  [INFO] Install with: pip install blpapi")
            mode = "manual"
        else:
            print("  Fetching curve from Bloomberg...")
            result["curve"] = fetch_curve_bloomberg(cfg)
            print(f"    → {len(result['curve'])} curve nodes")

            print("  Fetching vol surface from Bloomberg...")
            vol, exp_l, tnr_l = fetch_vol_surface_bloomberg(cfg)
            result["vol_surface"] = vol
            result["expiry_grid"] = labels_to_years(exp_l, EXPIRY_LABEL_TO_YEARS)
            result["tenor_grid"]  = labels_to_years(tnr_l, TENOR_LABEL_TO_YEARS)
            print(f"    → {vol.shape[0]}×{vol.shape[1]} surface")

            # NPV
            npv = fetch_swaption_npv_bloomberg(cfg)
            if npv is None:
                npv = cfg.get("benchmark", {}).get("npv")
            result["bbg_npv"] = npv

    if mode == "manual":
        manual_cfg = cfg.get("data_source", {}).get("manual", {})

        # Curve
        curve_file = manual_cfg.get("curve_file", "")
        curve_path = os.path.join(config_dir, curve_file) if curve_file else ""
        if curve_file and os.path.exists(curve_path):
            print(f"  Loading curve from {curve_file}")
            result["curve"] = load_curve_csv(curve_path)
        else:
            print("  Loading curve from config.yaml (inline)")
            result["curve"] = load_curve_yaml(cfg)
        print(f"    → {len(result['curve'])} curve nodes")

        # Vol surface
        vol_file = manual_cfg.get("vol_file", "")
        vol_path = os.path.join(config_dir, vol_file) if vol_file else ""
        if vol_file and os.path.exists(vol_path):
            print(f"  Loading vol surface from {vol_file}")
            vol, exp_l, tnr_l = load_vol_csv(vol_path)
        else:
            print("  Loading vol surface from config.yaml (inline)")
            vol, exp_l, tnr_l = load_vol_yaml(cfg)
        result["vol_surface"] = vol
        result["expiry_grid"] = labels_to_years(exp_l, EXPIRY_LABEL_TO_YEARS)
        result["tenor_grid"]  = labels_to_years(tnr_l, TENOR_LABEL_TO_YEARS)
        print(f"    → {vol.shape[0]}×{vol.shape[1]} surface")

        # NPV
        result["bbg_npv"] = cfg.get("benchmark", {}).get("npv")

    if result.get("bbg_npv") is None:
        raise ValueError("benchmark.npv is required in config (target NPV for inverse calibration)")

    return result

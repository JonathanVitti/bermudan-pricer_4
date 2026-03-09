#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cpg/bloomberg.py — Bloomberg data connector for CPG workspace.

Supports:
  - Mode LIVE: blpapi (when available on desk)
  - Mode FILE: CSV/Excel fallback (manual export from Terminal)

Data fetched:
  1. Swaption vol surface CAD ATM (normal vol, bp)
  2. OIS swap curve CAD CORRA (optional, can use QRM Staging)
  3. Supplementary market data

Usage:
    from cpg.bloomberg import BloombergConnector
    bbg = BloombergConnector(mode="file")
    vol_surface = bbg.load_vol_surface("path/to/swaption_vols.csv")
"""
import logging
import os
from typing import Optional, Dict, Any, Tuple

import numpy as np
import pandas as pd

log = logging.getLogger("cpg.bloomberg")

# ─── Standard swaption grid (CAD ATM) ────────────────────────────────────
# Expiries in years
EXPIRY_GRID = [1/12, 3/12, 6/12, 1, 2, 3, 5, 7, 10]
EXPIRY_LABELS = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y"]

# Tenors in years
TENOR_GRID = [1, 2, 3, 5, 7, 10, 15, 20, 30]
TENOR_LABELS = ["1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y"]

# ─── Bloomberg tickers for CAD swaption vol (normal, bp) ─────────────────
# Format: CADSB{expiry}{tenor} Curncy  (e.g., CADSB1Y5Y Curncy)
# These are ATM normal (Bachelier) vols in bp


def _build_vol_tickers() -> Dict[Tuple[str, str], str]:
    """Build mapping of (expiry, tenor) -> Bloomberg ticker."""
    tickers = {}
    for exp_lbl in EXPIRY_LABELS:
        for tnr_lbl in TENOR_LABELS:
            tickers[(exp_lbl, tnr_lbl)] = f"CADSB{exp_lbl}{tnr_lbl} Curncy"
    return tickers

VOL_TICKERS = _build_vol_tickers()


class BloombergConnector:
    """
    Bloomberg data interface for the CPG workspace.

    Modes:
      - 'live': uses blpapi to fetch real-time data
      - 'file': loads from CSV/Excel files (manual export)
      - 'proxy': generates synthetic vol surface from parameters
    """

    def __init__(self, mode: str = "file"):
        self.mode = mode.lower()
        self._session = None
        self._vol_surface = None   # (expiry_grid, tenor_grid, vol_matrix)
        self._vol_source = "none"  # "bloomberg", "file", "proxy"
        self._vol_as_of = None

        if self.mode == "live":
            self._init_blpapi()

    # ─── BLPAPI initialization ────────────────────────────────────────────

    def _init_blpapi(self):
        """Initialize Bloomberg API session."""
        try:
            import blpapi
            sessionOptions = blpapi.SessionOptions()
            sessionOptions.setServerHost("localhost")
            sessionOptions.setServerPort(8194)
            self._session = blpapi.Session(sessionOptions)
            if not self._session.start():
                log.warning("Bloomberg session failed to start. Falling back to file mode.")
                self.mode = "file"
                self._session = None
            else:
                if not self._session.openService("//blp/refdata"):
                    log.warning("Cannot open //blp/refdata. Falling back to file mode.")
                    self.mode = "file"
                    self._session = None
                else:
                    log.info("Bloomberg API connected successfully")
        except ImportError:
            log.warning("blpapi not installed. Install: pip install blpapi. Falling back to file mode.")
            self.mode = "file"
        except Exception as e:
            log.warning(f"Bloomberg connection failed: {e}. Falling back to file mode.")
            self.mode = "file"

    # ─── Vol surface: LIVE fetch ──────────────────────────────────────────

    def fetch_vol_surface_live(self, date: Optional[str] = None) -> pd.DataFrame:
        """
        Fetch swaption vol surface from Bloomberg via blpapi.

        Returns DataFrame with columns: Expiry, Tenor, Vol (bp normal).
        """
        if self._session is None:
            raise RuntimeError("Bloomberg session not available")

        import blpapi

        ref_data_service = self._session.getService("//blp/refdata")
        request = ref_data_service.createRequest("ReferenceDataRequest")

        # Add all swaption vol tickers
        for (exp, tnr), ticker in VOL_TICKERS.items():
            request.getElement("securities").appendValue(ticker)

        request.getElement("fields").appendValue("PX_LAST")

        if date:
            overrides = request.getElement("overrides")
            ov = overrides.appendElement()
            ov.setElement("fieldId", "REFERENCE_DATE")
            ov.setElement("value", date.replace("-", ""))

        self._session.sendRequest(request)

        results = {}
        while True:
            event = self._session.nextEvent(5000)
            for msg in event:
                if msg.hasElement("securityData"):
                    sec_data = msg.getElement("securityData")
                    for i in range(sec_data.numValues()):
                        security = sec_data.getValueAsElement(i)
                        ticker = security.getElementAsString("security")
                        fields = security.getElement("fieldData")
                        if fields.hasElement("PX_LAST"):
                            results[ticker] = fields.getElementAsFloat("PX_LAST")

            if event.eventType() == blpapi.Event.RESPONSE:
                break

        # Parse into structured DataFrame
        rows = []
        for (exp, tnr), ticker in VOL_TICKERS.items():
            if ticker in results:
                rows.append({"Expiry": exp, "Tenor": tnr, "Vol_bp": results[ticker]})

        df = pd.DataFrame(rows)
        log.info(f"Bloomberg vol surface: {len(df)} points fetched")

        self._vol_source = "bloomberg"
        self._vol_as_of = date or "live"
        self._parse_vol_df(df)

        return df

    # ─── Vol surface: FILE load ───────────────────────────────────────────

    def load_vol_surface(self, path: str) -> pd.DataFrame:
        """
        Load swaption vol surface from CSV or Excel file.

        Expected format - either:
          A) Long format: Expiry, Tenor, Vol (bp)
          B) Matrix format: rows=Expiry, cols=Tenor, values=Vol (bp)
        """
        ext = os.path.splitext(path)[1].lower()

        if ext in (".xlsx", ".xls"):
            df = pd.read_excel(path, engine="openpyxl")
        else:
            df = pd.read_csv(path)

        # Detect format
        cols_lower = [c.lower().strip() for c in df.columns]

        if "expiry" in cols_lower and "tenor" in cols_lower:
            # Long format
            df.columns = [c.strip() for c in df.columns]
            # Normalize column names
            col_map = {}
            for c in df.columns:
                cl = c.lower()
                if "expir" in cl:
                    col_map[c] = "Expiry"
                elif "tenor" in cl:
                    col_map[c] = "Tenor"
                elif "vol" in cl or "bp" in cl:
                    col_map[c] = "Vol_bp"
            df = df.rename(columns=col_map)

            if "Vol_bp" not in df.columns:
                # Try third column as vol
                remaining = [c for c in df.columns if c not in ("Expiry", "Tenor")]
                if remaining:
                    df = df.rename(columns={remaining[0]: "Vol_bp"})

        else:
            # Matrix format: first column is expiry labels, rest are tenor labels
            expiry_col = df.columns[0]
            tenor_cols = df.columns[1:]

            rows = []
            for _, row in df.iterrows():
                exp = str(row[expiry_col]).strip()
                for tnr_col in tenor_cols:
                    val = row[tnr_col]
                    if pd.notna(val):
                        rows.append({
                            "Expiry": exp,
                            "Tenor": str(tnr_col).strip(),
                            "Vol_bp": float(val),
                        })
            df = pd.DataFrame(rows)

        df["Vol_bp"] = pd.to_numeric(df["Vol_bp"], errors="coerce")
        df = df.dropna(subset=["Vol_bp"])

        self._vol_source = "file"
        self._vol_as_of = os.path.basename(path)
        self._parse_vol_df(df)

        log.info(f"Vol surface loaded from {path}: {len(df)} points")
        return df

    # ─── Vol surface: PROXY generation ────────────────────────────────────

    def generate_proxy_surface(
        self,
        vol_base_bp: float = 65.0,
        slope_per_year: float = -2.0,
        floor_bp: float = 30.0,
        smile_curvature: float = 0.0,
    ) -> pd.DataFrame:
        """
        Generate a synthetic vol surface from simple parameters.

        vol(expiry, tenor) = max(vol_base + slope * expiry + smile * (tenor - 5)^2, floor)

        Parameters in bp (normal vol, Bachelier).
        """
        rows = []
        for i, (exp_y, exp_lbl) in enumerate(zip(EXPIRY_GRID, EXPIRY_LABELS)):
            for j, (tnr_y, tnr_lbl) in enumerate(zip(TENOR_GRID, TENOR_LABELS)):
                vol = vol_base_bp + slope_per_year * exp_y + smile_curvature * (tnr_y - 5) ** 2
                vol = max(vol, floor_bp)
                rows.append({
                    "Expiry": exp_lbl,
                    "Tenor": tnr_lbl,
                    "Vol_bp": round(vol, 2),
                })

        df = pd.DataFrame(rows)
        self._vol_source = "proxy"
        self._vol_as_of = f"base={vol_base_bp}bp, slope={slope_per_year}"
        self._parse_vol_df(df)

        log.info(f"Proxy vol surface generated: {len(df)} points")
        return df

    # ─── Internal: parse vol DataFrame into grids ─────────────────────────

    def _parse_vol_df(self, df: pd.DataFrame):
        """Convert long-format vol DataFrame to grid arrays."""
        # Parse expiry/tenor labels to years
        def label_to_years(lbl):
            s = str(lbl).strip().upper()
            if s.endswith("M"):
                return float(s[:-1]) / 12.0
            elif s.endswith("Y"):
                return float(s[:-1])
            elif s.endswith("W"):
                return float(s[:-1]) / 52.0
            elif s.endswith("D"):
                return float(s[:-1]) / 365.0
            else:
                try:
                    return float(s)
                except ValueError:
                    return np.nan

        df = df.copy()
        df["Expiry_Y"] = df["Expiry"].apply(label_to_years)
        df["Tenor_Y"] = df["Tenor"].apply(label_to_years)
        df = df.dropna(subset=["Expiry_Y", "Tenor_Y"])

        exp_sorted = sorted(df["Expiry_Y"].unique())
        tnr_sorted = sorted(df["Tenor_Y"].unique())

        vol_matrix = np.full((len(exp_sorted), len(tnr_sorted)), np.nan)
        for _, row in df.iterrows():
            i = exp_sorted.index(row["Expiry_Y"])
            j = tnr_sorted.index(row["Tenor_Y"])
            vol_matrix[i, j] = row["Vol_bp"]

        # Fill NaN by nearest-neighbor interpolation
        from scipy.ndimage import generic_filter
        mask = np.isnan(vol_matrix)
        if mask.any():
            # Simple fill: use mean of non-NaN neighbors
            filled = vol_matrix.copy()
            for i in range(filled.shape[0]):
                for j in range(filled.shape[1]):
                    if np.isnan(filled[i, j]):
                        neighbors = []
                        for di in [-1, 0, 1]:
                            for dj in [-1, 0, 1]:
                                ni, nj = i + di, j + dj
                                if 0 <= ni < filled.shape[0] and 0 <= nj < filled.shape[1]:
                                    if not np.isnan(filled[ni, nj]):
                                        neighbors.append(filled[ni, nj])
                        filled[i, j] = np.mean(neighbors) if neighbors else vol_matrix[~mask].mean()
            vol_matrix = filled

        self._vol_surface = (
            np.array(exp_sorted),
            np.array(tnr_sorted),
            vol_matrix,
        )

    # ─── Accessors ────────────────────────────────────────────────────────

    @property
    def vol_source(self) -> str:
        return self._vol_source

    @property
    def vol_as_of(self) -> str:
        return self._vol_as_of or "N/A"

    @property
    def has_vol(self) -> bool:
        return self._vol_surface is not None

    def get_vol(self, expiry_years: float, tenor_years: float) -> float:
        """
        Interpolate vol (in bp) at given expiry and tenor.
        Uses bilinear interpolation on the grid.
        """
        if self._vol_surface is None:
            raise RuntimeError("No vol surface loaded. Call load_vol_surface() or generate_proxy_surface().")

        exp_grid, tnr_grid, vol_mat = self._vol_surface

        # Bilinear interpolation
        xc = float(np.clip(expiry_years, exp_grid[0], exp_grid[-1]))
        yc = float(np.clip(tenor_years, tnr_grid[0], tnr_grid[-1]))

        i1 = min(np.searchsorted(exp_grid, xc), len(exp_grid) - 1)
        j1 = min(np.searchsorted(tnr_grid, yc), len(tnr_grid) - 1)
        i0, j0 = max(i1 - 1, 0), max(j1 - 1, 0)

        wx = 0 if exp_grid[i1] == exp_grid[i0] else (xc - exp_grid[i0]) / (exp_grid[i1] - exp_grid[i0])
        wy = 0 if tnr_grid[j1] == tnr_grid[j0] else (yc - tnr_grid[j0]) / (tnr_grid[j1] - tnr_grid[j0])

        return float(
            (1 - wy) * ((1 - wx) * vol_mat[i0, j0] + wx * vol_mat[i1, j0]) +
            wy * ((1 - wx) * vol_mat[i0, j1] + wx * vol_mat[i1, j1])
        )

    def get_vol_matrix(self) -> Optional[Dict[str, Any]]:
        """Return the full vol surface for display/export."""
        if self._vol_surface is None:
            return None

        exp_grid, tnr_grid, vol_mat = self._vol_surface
        return {
            "expiry_grid": exp_grid.tolist(),
            "tenor_grid": tnr_grid.tolist(),
            "vol_matrix": vol_mat.tolist(),
            "source": self._vol_source,
            "as_of": self._vol_as_of,
        }

    def get_vol_surface_df(self) -> Optional[pd.DataFrame]:
        """Return vol surface as long-format DataFrame."""
        if self._vol_surface is None:
            return None

        exp_grid, tnr_grid, vol_mat = self._vol_surface
        rows = []
        for i, exp in enumerate(exp_grid):
            for j, tnr in enumerate(tnr_grid):
                rows.append({
                    "Expiry_Y": exp,
                    "Tenor_Y": tnr,
                    "Vol_bp": vol_mat[i, j],
                    "Source": self._vol_source,
                })
        return pd.DataFrame(rows)

    # ─── Cleanup ──────────────────────────────────────────────────────────

    def close(self):
        if self._session is not None:
            self._session.stop()
            self._session = None

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cpg/export.py — Export des résultats de pricing CPG.
"""
import logging
from typing import Optional

import pandas as pd

log = logging.getLogger("cpg.export")


def export_results(
    df: pd.DataFrame,
    path: str,
    fmt: str = "xlsx",
    include_summary: bool = True,
) -> str:
    """
    Export pricing results to file.

    Parameters
    ----------
    df : pd.DataFrame
        Results from price_cpg_portfolio.
    path : str
        Output file path.
    fmt : str
        'csv' or 'xlsx'.
    include_summary : bool
        If xlsx, add a summary sheet.

    Returns
    -------
    str: path written.
    """
    # Drop Cashflows column for flat export
    export_cols = [c for c in df.columns if c != "Cashflows"]
    flat = df[export_cols].copy()

    if fmt == "csv":
        flat.to_csv(path, index=False, encoding="utf-8-sig")
        log.info(f"Résultats exportés: {path} ({len(flat)} lignes)")
        return path

    # Excel with optional summary sheet
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        flat.to_excel(writer, sheet_name="Résultats", index=False)

        if include_summary:
            ok = flat[flat["Status"] == "OK"]
            summary_data = {
                "Métrique": [
                    "Date d'évaluation",
                    "Nombre de trades",
                    "Trades OK",
                    "Trades en erreur",
                    "Trades échus",
                    "PV Total (CAD)",
                    "PV Coupons Total",
                    "PV Principal Total",
                    "Duration moyenne (pondérée)",
                    "Montant notionnel total",
                ],
                "Valeur": [
                    flat["EvalDate"].iloc[0] if len(flat) > 0 else "",
                    len(flat),
                    len(ok),
                    (flat["Status"].str.startswith("ERROR")).sum(),
                    (flat["Status"] == "MATURED").sum(),
                    f"{ok['PV'].sum():,.2f}",
                    f"{ok['PV_Coupons'].sum():,.2f}",
                    f"{ok['PV_Principal'].sum():,.2f}",
                    f"{(ok['Duration_Approx'] * ok['PV']).sum() / ok['PV'].sum():.4f}" if ok['PV'].sum() > 0 else "N/A",
                    f"{ok['Montant'].sum():,.2f}",
                ],
            }
            pd.DataFrame(summary_data).to_excel(writer, sheet_name="Sommaire", index=False)

        # Auto-adjust column widths
        for sheet_name in writer.sheets:
            ws = writer.sheets[sheet_name]
            for col_idx, col in enumerate(ws.iter_cols(min_row=1, max_row=1), 1):
                max_len = max(len(str(cell.value or "")) for cell in ws[col[0].column_letter])
                ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 30)

    log.info(f"Résultats exportés: {path} ({len(flat)} lignes, format {fmt})")
    return path

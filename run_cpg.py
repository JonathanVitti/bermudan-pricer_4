#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_cpg.py — Point d'entrée CLI pour le pricing de portefeuille CPG.

Usage:
    python run_cpg.py --eval-date 2026-02-26 --trades-file data/trades_sample.xlsx --curve-file data/curve_sample.csv --out results.xlsx
    python run_cpg.py --eval-date 2026-02-26 --trades-file data/trades_sample.xlsx --curve-sql --out results.xlsx

Options:
    --eval-date     Date d'évaluation (YYYY-MM-DD)
    --trades-file   Fichier des transactions CPG (.csv ou .xlsx)
    --curve-file    Fichier courbe CDF (.csv) — alternative à SQL
    --curve-sql     Récupérer la courbe depuis SQL
    --out           Fichier de sortie (.xlsx ou .csv)
    --verbose       Logging détaillé
"""
import os, sys, argparse, logging, time

# Add src/ to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))


def main():
    parser = argparse.ArgumentParser(
        description="CPG Portfolio Pricer — Desjardins",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--eval-date", required=True, help="Date d'évaluation (YYYY-MM-DD)")
    parser.add_argument("--trades-file", required=True, help="Fichier des trades CPG (.csv/.xlsx)")
    parser.add_argument("--curve-file", help="Fichier courbe CDF (.csv)")
    parser.add_argument("--curve-sql", action="store_true", help="Récupérer la courbe depuis SQL")
    parser.add_argument("--out", default="output/cpg_results.xlsx", help="Fichier de sortie")
    parser.add_argument("--verbose", "-v", action="store_true", help="Logging détaillé")
    args = parser.parse_args()

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("run_cpg")

    print("=" * 64)
    print("  CPG Portfolio Pricer — Desjardins")
    print(f"  Eval date: {args.eval_date}")
    print("=" * 64)

    t0 = time.time()

    # ─── 1. Load trades ──────────────────────────────────────────────
    from cpg.trades import load_trades_file
    log.info(f"Chargement des trades: {args.trades_file}")
    trades_df = load_trades_file(args.trades_file)
    log.info(f"  → {len(trades_df)} trades chargés")

    # ─── 2. Load curve ───────────────────────────────────────────────
    if args.curve_sql:
        from cpg.curve_sql import fetch_funding_curve
        log.info("Récupération de la courbe depuis SQL...")
        curve_df = fetch_funding_curve(args.eval_date)
    elif args.curve_file:
        from cpg.curve_sql import load_curve_from_csv
        log.info(f"Chargement de la courbe: {args.curve_file}")
        curve_df = load_curve_from_csv(args.curve_file)
    else:
        parser.error("Spécifier --curve-file ou --curve-sql")

    log.info(f"  → {len(curve_df)} points de courbe")

    # ─── 3. Price portfolio ──────────────────────────────────────────
    from cpg.pricing import price_cpg_portfolio
    log.info("Pricing du portefeuille...")
    results_df = price_cpg_portfolio(trades_df, curve_df, args.eval_date)

    # ─── 4. Export ───────────────────────────────────────────────────
    from cpg.export import export_results
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    fmt = "csv" if args.out.endswith(".csv") else "xlsx"
    export_results(results_df, args.out, fmt=fmt)

    elapsed = time.time() - t0
    print(f"\n{'=' * 64}")
    print(f"  Terminé en {elapsed:.1f}s")
    print(f"  {len(results_df)} trades pricés")

    ok = results_df[results_df["Status"] == "OK"]
    if len(ok) > 0:
        print(f"  PV total: {ok['PV'].sum():,.2f} CAD")

    print(f"  Résultats: {args.out}")
    print(f"{'=' * 64}")


if __name__ == "__main__":
    main()

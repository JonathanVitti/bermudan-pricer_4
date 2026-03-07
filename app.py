#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py — Portail unifié Desjardins: Bermudan Swaption + Épargne à terme Pricer.

Lancement:
    python app.py                → http://localhost:5050
    python app.py --port 8080    → http://localhost:8080
    PORT=8080 python app.py      → http://localhost:8080
"""
import os, sys, json, webbrowser, threading, tempfile, io, argparse, socket
from datetime import datetime
from contextlib import redirect_stdout
from sqlalchemy import create_engine, text

src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from flask import Flask, request, jsonify, send_file, send_from_directory, redirect
import yaml, numpy as np, openpyxl

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ─────────────────────────────────────────────────────────────────────────────
# Feature flag: True = module Bermudan visible, False = mode 100% CPG
# ─────────────────────────────────────────────────────────────────────────────
SHOW_BERMUDAN = False

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
app.secret_key = os.urandom(24)  # Session security


# ═══════════════════════════════════════════════════════════════════════════
#  DECORATORS — éliminent la duplication dans les routes
# ═══════════════════════════════════════════════════════════════════════════

from functools import wraps

def require_curve(f):
    """Décorateur: vérifie que la courbe CDF est chargée."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        if app.config.get("CPG_CURVE") is None:
            return jsonify({"error": "Courbe non chargée. Charger la courbe CDF à l'étape 1."}), 400
        return f(*args, **kwargs)
    return wrapped

def require_curve_and_trades(f):
    """Décorateur: vérifie courbe + trades chargés."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        if app.config.get("CPG_CURVE") is None:
            return jsonify({"error": "Courbe non chargée."}), 400
        if app.config.get("CPG_TRADES") is None:
            return jsonify({"error": "Trades non chargés."}), 400
        return f(*args, **kwargs)
    return wrapped


def validate_deal(data):
    """
    Valide les paramètres d'un deal d'épargne à terme.
    Retourne (clean_data, None) si OK, (None, error_msg) si invalide.
    """
    errors = []
    clean = {}

    # Type
    clean["cpg_type"] = data.get("cpg_type", "COUPON")
    if clean["cpg_type"] not in ("COUPON", "LINEAR ACCRUAL"):
        errors.append(f"Type invalide: {clean['cpg_type']}. Attendu: COUPON ou LINEAR ACCRUAL.")

    # Notional
    try:
        clean["notional"] = float(data.get("notional", 0))
        if clean["notional"] <= 0:
            errors.append(f"Nominal invalide: {clean['notional']}. Doit être > 0.")
        if clean["notional"] > 1e12:
            errors.append(f"Nominal suspect: {clean['notional']:,.0f}. Vérifiez la saisie.")
    except (ValueError, TypeError):
        errors.append(f"Nominal non numérique: {data.get('notional')}")

    # Client rate
    try:
        clean["client_rate"] = float(data.get("client_rate", 0))
        if clean["client_rate"] < 0 or clean["client_rate"] > 25:
            errors.append(f"Taux client suspect: {clean['client_rate']}%. Attendu: 0-25%.")
    except (ValueError, TypeError):
        errors.append(f"Taux client non numérique: {data.get('client_rate')}")

    # Dates
    for field, label in [("emission", "Émission"), ("initial_maturity", "Échéance initiale"),
                         ("final_maturity", "Échéance finale")]:
        val = data.get(field, "")
        if not val:
            errors.append(f"{label} manquante.")
        else:
            try:
                datetime.strptime(val, "%Y-%m-%d")
                clean[field] = val
            except ValueError:
                errors.append(f"{label} invalide: {val}. Format attendu: AAAA-MM-JJ.")

    # Date coherence
    if "emission" in clean and "initial_maturity" in clean and "final_maturity" in clean:
        em = datetime.strptime(clean["emission"], "%Y-%m-%d")
        im = datetime.strptime(clean["initial_maturity"], "%Y-%m-%d")
        fm = datetime.strptime(clean["final_maturity"], "%Y-%m-%d")
        if im <= em:
            errors.append("Échéance initiale doit être après l'émission.")
        if fm <= im:
            errors.append("Échéance finale doit être après l'initiale.")
        if (fm - em).days > 365 * 40:
            errors.append(f"Durée suspecte: {(fm-em).days/365:.0f} ans. Vérifiez les dates.")

    # Freq
    try:
        clean["freq_per_year"] = int(data.get("freq_per_year", 1))
    except (ValueError, TypeError):
        clean["freq_per_year"] = 1

    # Passthrough
    clean["fundserv"] = data.get("fundserv", "")
    clean["eval_date"] = data.get("eval_date", "")
    clean["cusip"] = data.get("cusip", "")

    if errors:
        return None, " | ".join(errors)
    return clean, None

@app.route("/fonts/<path:filename>")
def serve_font(filename):
    return send_from_directory(os.path.join(BASE_DIR, "fonts"), filename)

@app.route("/static/<path:filename>")
def serve_static(filename):
    return send_from_directory(os.path.join(BASE_DIR, "static"), filename)

@app.route("/d15-desjardins-logo-couleur.png")
def serve_logo():
    return send_from_directory(os.path.join(BASE_DIR, "static"), "d15-desjardins-logo-couleur.png")

# ═══════════════════════════════════════════════════════════════════════════
#  SHARED CSS + HEADER — used by both pages
# ═══════════════════════════════════════════════════════════════════════════

SHARED_HEAD = r"""<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="icon" type="image/png" href="/static/alveole-32.png">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
@font-face{font-family:'Desjardins Sans';src:url('/fonts/DesjardinsSans-Regular.woff2') format('woff2');font-weight:400;font-style:normal;font-display:swap}
@font-face{font-family:'Desjardins Sans';src:url('/fonts/DesjardinsSans-Bold.woff2') format('woff2');font-weight:700;font-style:normal;font-display:swap}
:root{--dj-green:#00874E;--dj-black:#383838;--dj-white:#FFFFFF;--dj-mint:#CCE7DC;--dj-grey:#E6E7E8;--bg:#f5f5f7;--bg2:#ffffff;--bg3:#fafafa;--card:#ffffff;--border:rgba(0,0,0,.06);--border-hi:var(--dj-green);--text:#1d1d1f;--text2:#6e6e73;--text3:#86868b;--accent:var(--dj-green);--accent2:#00a463;--green:var(--dj-green);--green-bg:rgba(0,135,78,0.06);--green-subtle:rgba(0,135,78,0.03);--red:#ff3b30;--red-bg:rgba(255,59,48,0.06);--amber:#ff9500;--amber-bg:rgba(255,149,0,0.06);--shadow-sm:0 1px 3px rgba(0,0,0,.04),0 1px 2px rgba(0,0,0,.06);--shadow:0 4px 16px rgba(0,0,0,.06),0 1px 3px rgba(0,0,0,.04);--radius:14px;--radius-sm:10px;--sans:'Desjardins Sans',-apple-system,BlinkMacSystemFont,'SF Pro Display',system-ui,sans-serif;--mono:'JetBrains Mono','SF Mono',ui-monospace,monospace}
*{margin:0;padding:0;box-sizing:border-box}
html{-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;text-rendering:optimizeLegibility}
body{font-family:var(--sans);background:var(--bg);color:var(--text);min-height:100vh;line-height:1.5}
::selection{background:var(--dj-mint);color:var(--dj-black)}
.header{position:sticky;top:0;z-index:50;background:rgba(255,255,255,0.72);backdrop-filter:saturate(180%) blur(20px);-webkit-backdrop-filter:saturate(180%) blur(20px);border-bottom:1px solid rgba(0,0,0,0.06);padding:0 32px;display:flex;align-items:center;justify-content:space-between;height:56px}
.header-left{display:flex;align-items:center;gap:14px}
.header-logo{height:32px}
.header h1{font-family:var(--sans);font-size:17px;font-weight:700;letter-spacing:-.3px;color:var(--text)}
.header h1 em{color:var(--accent);font-style:normal}
.header .subtitle{font-size:10px;color:var(--text3);font-family:var(--mono);letter-spacing:.2px}
.nav-tabs{display:flex;gap:2px;height:100%}
.nav-tab{display:flex;align-items:center;padding:0 18px;font-size:13px;font-weight:600;color:var(--text3);text-decoration:none;border-bottom:2px solid transparent;transition:all .2s}
.nav-tab:hover{color:var(--text)}.nav-tab.active{color:var(--accent);border-bottom-color:var(--accent)}
.container{max-width:1480px;margin:0 auto;padding:28px 32px;display:grid;grid-template-columns:440px 1fr;gap:28px}
.container-single{max-width:960px;margin:0 auto;padding:32px}
.panel{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;box-shadow:var(--shadow-sm);transition:box-shadow .25s ease}
.panel:hover{box-shadow:var(--shadow)}
.panel-header{padding:16px 22px;border-bottom:1px solid var(--border);font-family:var(--sans);font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1.2px;color:var(--text3);display:flex;align-items:center;gap:10px;background:var(--bg3)}
.panel-header .dot{width:7px;height:7px;border-radius:50%;background:var(--accent);box-shadow:0 0 0 3px rgba(0,135,78,0.12)}
.panel-body{padding:20px 22px}
.section-label{font-family:var(--sans);font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1.8px;color:var(--accent);margin:20px 0 10px;padding-bottom:6px;border-bottom:1px solid rgba(0,135,78,0.08)}.section-label:first-child{margin-top:0}
.field{margin-bottom:10px;display:grid;grid-template-columns:140px 1fr;align-items:center;gap:10px}
.field label{font-family:var(--sans);font-size:13px;color:var(--text2);font-weight:500}
.field input,.field select{background:var(--bg);border:1px solid var(--border);border-radius:var(--radius-sm);padding:9px 12px;color:var(--text);font-family:var(--mono);font-size:12px;outline:none;transition:border-color .2s,box-shadow .2s}
.field input:focus,.field select:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(0,135,78,0.1)}
.field select{cursor:pointer;-webkit-appearance:none;appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%2386868b' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 10px center;padding-right:28px}
.field-check{margin-bottom:10px;display:flex;align-items:center;gap:10px}
.field-check label{font-family:var(--sans);font-size:13px;color:var(--text2);font-weight:400;cursor:pointer}
.field-check input[type=checkbox]{width:18px;height:18px;cursor:pointer;accent-color:var(--accent);border-radius:4px}
.upload-zone{border:2px dashed rgba(0,135,78,0.2);border-radius:var(--radius);padding:24px;text-align:center;cursor:pointer;transition:all 0.3s;margin:12px 0;background:var(--green-subtle)}
.upload-zone:hover{border-color:var(--accent);background:rgba(0,135,78,0.05);transform:translateY(-1px);box-shadow:var(--shadow)}
.upload-zone.loaded,.upload-zone.ok{border-color:var(--green);border-style:solid;background:var(--green-bg)}
.upload-zone .icon{font-size:28px;margin-bottom:6px}.upload-zone .label{font-family:var(--sans);font-size:14px;color:var(--text2);font-weight:500}
.upload-zone .sublabel{font-size:12px;color:var(--text3);margin-top:4px}
.upload-zone.loaded .label,.upload-zone.ok .label{color:var(--green);font-weight:600}.upload-zone input[type=file]{display:none}
.file-info{font-family:var(--mono);font-size:11px;color:var(--green);padding:10px 14px;background:var(--green-bg);border-radius:var(--radius-sm);margin-top:8px;display:none;border:1px solid rgba(0,135,78,0.1)}
.file-info.show{display:block}
.btn-price,.btn{display:inline-block;padding:14px 28px;background:var(--dj-green);color:var(--dj-white);border:none;border-radius:var(--radius-sm);font-size:15px;font-weight:700;cursor:pointer;transition:all 0.25s;font-family:var(--sans);letter-spacing:.3px}
.btn-price{width:100%;margin-top:16px}
.btn-price:hover,.btn:hover{transform:translateY(-2px);box-shadow:0 8px 28px rgba(0,135,78,0.35);background:#007a46}
.btn-price:active,.btn:active{transform:translateY(0);box-shadow:var(--shadow)}
.btn-price:disabled,.btn:disabled{opacity:.4;cursor:not-allowed;transform:none;box-shadow:none}
.btn-price.running{background:var(--text3);animation:pulse 1.5s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.6}}
.btn-export,.btn-sec{padding:12px 24px;background:var(--card);border:1px solid var(--border);color:var(--text2);border-radius:var(--radius-sm);font-size:13px;cursor:pointer;font-family:var(--sans);font-weight:600;transition:all .2s;box-shadow:var(--shadow-sm);display:inline-block}
.btn-export:hover,.btn-sec:hover{border-color:var(--accent);color:var(--accent);box-shadow:var(--shadow);transform:translateY(-1px)}
.data-section{margin-top:12px}.data-toggle{font-family:var(--sans);font-size:12px;color:var(--accent);cursor:pointer;padding:6px 0;font-weight:500;transition:color .2s}
.data-toggle:hover{color:#006f40}.data-area{display:none;margin-top:6px}.data-area.open{display:block}
.data-area textarea{width:100%;height:160px;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius-sm);padding:12px;color:var(--text);font-family:var(--mono);font-size:11px;line-height:1.6;resize:vertical;outline:none;transition:border-color .2s}
.data-area textarea:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(0,135,78,0.08)}.data-area label{display:block;font-family:var(--sans);font-size:11px;color:var(--text3);margin-bottom:4px}
.results-area{display:flex;flex-direction:column;gap:16px}
.result-cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.rcard{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:18px;text-align:center;transition:all .25s;box-shadow:var(--shadow-sm)}
.rcard:hover{border-color:rgba(0,135,78,0.2);box-shadow:var(--shadow);transform:translateY(-1px)}
.rcard .label{font-family:var(--sans);font-size:10px;text-transform:uppercase;letter-spacing:1.8px;color:var(--text3);margin-bottom:6px;font-weight:600}
.rcard .value{font-family:var(--mono);font-size:22px;font-weight:600;color:var(--text)}
.rcard .value.match{color:var(--green)}.rcard .sub{font-size:11px;color:var(--text3);margin-top:4px;font-family:var(--mono)}
.cmp-table,.results-table{width:100%;border-collapse:collapse;font-size:13px}
.cmp-table th,.results-table th{text-align:left;padding:10px 16px;font-family:var(--sans);font-size:10px;text-transform:uppercase;letter-spacing:1.2px;color:var(--text3);border-bottom:2px solid var(--border);font-weight:700;background:var(--bg3)}
.cmp-table td,.results-table td{padding:11px 16px;border-bottom:1px solid var(--border);font-family:var(--mono);font-size:12px}
.cmp-table tr:hover,.results-table tr:hover{background:var(--green-subtle)}
.cmp-table .name,.results-table .name{color:var(--text);font-family:var(--sans);font-weight:500}
.cmp-table .val{color:var(--text);text-align:right;font-weight:600}.cmp-table .bbg{color:var(--text2);text-align:right}.cmp-table .diff{text-align:right}
.diff-good{color:var(--green);font-weight:600}.diff-ok{color:var(--amber);font-weight:600}.diff-bad{color:var(--red);font-weight:600}
.model-bar{display:flex;gap:20px;padding:14px 22px;font-family:var(--mono);font-size:12px;color:var(--text2);flex-wrap:wrap;background:var(--bg3);border-bottom:1px solid var(--border)}
.model-bar span{color:var(--accent);font-weight:600}
.log-area{background:var(--bg);border:1px solid var(--border);border-radius:var(--radius-sm);padding:16px;font-family:var(--mono);font-size:11px;line-height:1.7;color:var(--text3);max-height:220px;overflow-y:auto;white-space:pre-wrap}
.placeholder{display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:400px;color:var(--text3);gap:14px;background:var(--card);border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow-sm)}
.placeholder svg{opacity:.2;stroke:var(--text3)}.placeholder p{font-size:14px;color:var(--text3)}
.summary{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:16px 0}
.scard{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px;text-align:center;box-shadow:var(--shadow-sm)}
.scard .lbl{font-size:10px;text-transform:uppercase;letter-spacing:1.5px;color:var(--text3);margin-bottom:4px}
.scard .val{font-family:var(--mono);font-size:20px;font-weight:700;color:var(--accent)}
.scard .sub{font-size:10px;color:var(--text3);margin-top:2px}
.status{font-family:var(--mono);font-size:12px;padding:8px 14px;border-radius:8px;margin-top:10px;display:none}
.status.show{display:block}.status.ok{background:var(--green-bg);color:var(--green)}.status.err{background:var(--red-bg);color:var(--red)}
.scroll-left{max-height:calc(100vh - 84px);overflow-y:auto;padding-right:4px}
.scroll-left::-webkit-scrollbar{width:6px}.scroll-left::-webkit-scrollbar-track{background:transparent}.scroll-left::-webkit-scrollbar-thumb{background:rgba(0,0,0,.1);border-radius:99px}
@media(max-width:900px){.container{grid-template-columns:1fr;padding:16px}.result-cards,.summary{grid-template-columns:1fr 1fr}}
</style>"""

def _header_html(active, extra_right=""):
    tabs = []
    if SHOW_BERMUDAN:
        brm = ' class="nav-tab active"' if active == "bermudan" else ' class="nav-tab"'
        tabs.append(f'<a href="/"{brm}>Bermudan Swaption</a>')
    cpg = ' class="nav-tab active"' if active == "cpg" else ' class="nav-tab"'
    tabs.append(f'<a href="/cpg"{cpg}>Épargne à terme</a>')
    tabs_html = "".join(tabs)
    return f'''<div class="header">
<div class="header-left"><img src="/d15-desjardins-logo-couleur.png" alt="Desjardins" class="header-logo" onerror="this.style.display='none'">
<div><h1>Desjardins <em>Analytics</em></h1><div class="subtitle">Portail de pricing · Produits dérivés &amp; Épargne à terme</div></div></div>
<nav class="nav-tabs">{tabs_html}</nav>
<div>{extra_right}</div></div>'''


# ═══════════════════════════════════════════════════════════════════════════
#  PAGE BODIES (HTML+JS for each pricer)
# ═══════════════════════════════════════════════════════════════════════════

BERMUDAN_BODY = r"""
<div class="container">
<div class="scroll-left">
<div class="panel">
<div class="panel-header"><div class="dot"></div> Deal Parameters</div>
<div class="panel-body">
<div class="section-label">Deal</div>
<div class="field"><label>Valuation Date</label><input type="date" id="val_date" value="2026-02-11"></div>
<div class="field"><label>Notional</label><input type="number" id="notional" value="10000000" step="1000000"></div>
<div class="field"><label>Strike (%)</label><input type="number" id="strike" value="3.245112" step="0.000001"></div>
<div class="field"><label>Direction</label><select id="direction"><option value="Receiver">Receiver</option><option value="Payer">Payer</option></select></div>
<div class="field"><label>Swap Start</label><input type="date" id="swap_start" value="2027-02-12"></div>
<div class="field"><label>Swap End</label><input type="date" id="swap_end" value="2032-02-12"></div>
<div class="field"><label>Frequency</label><select id="frequency"><option value="SemiAnnual" selected>SemiAnnual</option><option value="Quarterly">Quarterly</option><option value="Annual">Annual</option></select></div>
<div class="field"><label>Day Count</label><select id="day_count"><option value="ACT/365" selected>ACT/365</option><option value="ACT/360">ACT/360</option><option value="30/360">30/360</option></select></div>
<div class="field"><label>Payment Lag</label><input type="number" id="payment_lag" value="2"></div>
<div class="field"><label>Currency</label><input type="text" id="currency" value="CAD"></div>
<div class="section-label">Model</div>
<div class="field"><label>Mean Reversion</label><input type="number" id="mean_rev" value="0.03" step="0.001"></div>
<div class="field-check"><input type="checkbox" id="calib_a"><label for="calib_a">Calibrate a (mean reversion)</label></div>
<div class="field"><label>FDM Grid</label><input type="number" id="fdm_grid" value="300"></div>
<div class="section-label">Calibration Mode</div>
<div class="field-check"><input type="checkbox" id="standalone_mode" onchange="toggleBBG()"><label for="standalone_mode">Standalone (no BBG)</label></div>
<div id="bbgSection">
<div class="section-label">BBG Valuation</div>
<div class="field"><label>NPV</label><input type="number" id="bbg_npv" value="255683.06" step="0.01"></div>
<div class="field"><label>ATM Strike (%)</label><input type="number" id="bbg_atm" value="2.922733" step="0.000001"></div>
<div class="field"><label>Yield Value (bp)</label><input type="number" id="bbg_yv" value="56.389" step="0.001"></div>
<div class="field"><label>Und. Premium (%)</label><input type="number" id="bbg_uprem" value="1.46175" step="0.00001"></div>
<div class="field"><label>Premium (%)</label><input type="number" id="bbg_prem" value="2.55683" step="0.00001"></div>
<div class="section-label">BBG Greeks</div>
<div class="field"><label>DV01</label><input type="number" id="bbg_dv01" value="2832.42" step="0.01"></div>
<div class="field"><label>Gamma (1bp)</label><input type="number" id="bbg_gamma" value="22.06" step="0.01"></div>
<div class="field"><label>Vega (1bp)</label><input type="number" id="bbg_vega" value="2542.10" step="0.01"></div>
<div class="field"><label>Theta (1 day)</label><input type="number" id="bbg_theta" value="-109.14" step="0.01"></div>
</div></div></div>
<div class="panel" style="margin-top:14px">
<div class="panel-header"><div class="dot" style="background:var(--amber)"></div> Market Data</div>
<div class="panel-body">
<div class="upload-zone" id="uploadZone" onclick="document.getElementById('fileInput').click()">
<div class="icon">📁</div><div class="label">Click to load market data (.xlsx)</div>
<div class="sublabel">Excel with sheets: Curve_CAD_OIS + BVOL_CAD_RFR_Normal</div>
<input type="file" id="fileInput" accept=".xlsx,.xls" onchange="uploadFile(this)">
</div>
<div class="file-info" id="fileInfo"></div>
<div class="data-section"><div class="data-toggle" onclick="toggleData('curve')">▸ Manual: Curve Data</div>
<div class="data-area" id="curveData"><label>date,discount_factor (one per line)</label><textarea id="curveText"></textarea></div></div>
<div class="data-section"><div class="data-toggle" onclick="toggleData('vol')">▸ Manual: Vol Surface</div>
<div class="data-area" id="volData"><label>BPx10 matrix</label><textarea id="volText"></textarea></div></div>
<button class="btn-price" id="btnPrice" onclick="runPricer()">▶ PRICE</button>
</div></div></div>
<div class="results-area" id="resultsArea">
<div class="placeholder"><svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 3v18h18"/><path d="M7 16l4-8 4 4 4-6"/></svg>
<p>Load market data, set deal parameters, click <strong>PRICE</strong></p></div>
</div></div>
<script>
const EXPIRY_LABELS=["1Mo","3Mo","6Mo","9Mo","1Yr","2Yr","3Yr","4Yr","5Yr","6Yr","7Yr","8Yr","9Yr","10Yr","12Yr","15Yr","20Yr","25Yr","30Yr"];
const TENOR_LABELS=["1Y","2Y","3Y","4Y","5Y","6Y","7Y","8Y","9Y","10Y","12Y","15Y","20Y","25Y","30Y"];
let loadedExpLabels=null;
function toggleBBG(){document.getElementById('bbgSection').style.display=document.getElementById('standalone_mode').checked?'none':'block'}
function toggleData(id){const el=document.getElementById(id+'Data');el.classList.toggle('open');const t=el.previousElementSibling;t.textContent=(el.classList.contains('open')?'▾':'▸')+t.textContent.slice(1)}
function fmt(n,dec=2){if(n===null||n===undefined)return'N/A';return parseFloat(n).toLocaleString('en-US',{minimumFractionDigits:dec,maximumFractionDigits:dec})}
function diffClass(pct){const a=Math.abs(pct);if(a<3)return'diff-good';if(a<10)return'diff-ok';return'diff-bad'}
function diffBpClass(d,r){const p=r?Math.abs(d/r*100):0;if(p<3)return'diff-good';if(p<10)return'diff-ok';return'diff-bad'}
function uploadFile(input){const file=input.files[0];if(!file)return;const fd=new FormData();fd.append('file',file);const info=document.getElementById('fileInfo'),zone=document.getElementById('uploadZone');info.className='file-info show';info.textContent='⟳ Reading '+file.name+'...';info.style.color='var(--amber)';info.style.background='var(--amber-bg)';fetch('/api/upload_excel',{method:'POST',body:fd}).then(r=>r.json()).then(data=>{if(data.error){info.textContent='✗ '+data.error;info.style.color='var(--red)';info.style.background='var(--red-bg)';return}loadedExpLabels=data.expiry_labels;document.getElementById('curveText').value=data.curve.map(r=>r[0]+','+r[1]).join('\n');document.getElementById('volText').value=data.vol_values.map(r=>r.join(',')).join('\n');zone.classList.add('loaded');zone.querySelector('.icon').textContent='✓';zone.querySelector('.label').textContent=file.name;zone.querySelector('.sublabel').textContent='Click to load a different file';info.textContent='✓ Loaded '+data.curve.length+' nodes + '+data.vol_values.length+'×'+data.vol_values[0].length+' vol';info.style.color='var(--green)';info.style.background='var(--green-bg)';}).catch(err=>{info.textContent='✗ '+err;info.style.color='var(--red)';info.style.background='var(--red-bg)'})}
function runPricer(){const btn=document.getElementById('btnPrice');btn.disabled=true;btn.classList.add('running');btn.textContent='⟳ PRICING...';const vL=document.getElementById('volText').value.trim().split('\n'),vV=vL.filter(l=>l.trim()).map(l=>l.split(/[,\t]+/).map(Number)),cL=document.getElementById('curveText').value.trim().split('\n'),cD=cL.filter(l=>l.trim()).map(l=>{const p=l.split(/[,\t]+/);return[p[0].trim(),parseFloat(p[1])]}),eL=loadedExpLabels||EXPIRY_LABELS.slice(0,vV.length),tL=TENOR_LABELS.slice(0,vV[0]?vV[0].length:15),sa=document.getElementById('standalone_mode').checked,bbg=sa?{npv:0,atm_strike:0,yield_value_bp:0,underlying_premium:0,premium:0,dv01:0,gamma_1bp:0,vega_1bp:0,theta_1d:0}:{npv:+document.getElementById('bbg_npv').value||0,atm_strike:+document.getElementById('bbg_atm').value||0,yield_value_bp:+document.getElementById('bbg_yv').value||0,underlying_premium:+document.getElementById('bbg_uprem').value||0,premium:+document.getElementById('bbg_prem').value||0,dv01:+document.getElementById('bbg_dv01').value||0,gamma_1bp:+document.getElementById('bbg_gamma').value||0,vega_1bp:+document.getElementById('bbg_vega').value||0,theta_1d:+document.getElementById('bbg_theta').value||0};fetch('/api/price',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({deal:{valuation_date:document.getElementById('val_date').value,notional:+document.getElementById('notional').value,strike:+document.getElementById('strike').value,direction:document.getElementById('direction').value,swap_start:document.getElementById('swap_start').value,swap_end:document.getElementById('swap_end').value,fixed_frequency:document.getElementById('frequency').value,day_count:document.getElementById('day_count').value,payment_lag:parseInt(document.getElementById('payment_lag').value),currency:document.getElementById('currency').value},model:{mean_reversion:+document.getElementById('mean_rev').value,calibrate_a:document.getElementById('calib_a').checked,fdm_time_grid:parseInt(document.getElementById('fdm_grid').value),fdm_space_grid:parseInt(document.getElementById('fdm_grid').value)},benchmark:bbg,curve_data:cD,vol_surface_data:{expiry_labels:eL,tenor_labels:tL,values:vV},exercise:{mode:"auto"},data_source:{mode:"manual"},greeks:{dv01_bump_bp:1,gamma_bump_bp:1,vega_bump_bp:1,compute_theta:true,theta_annualization:"none"},output:{print_console:false,export_excel:false}})}).then(r=>r.json()).then(data=>{btn.disabled=false;btn.classList.remove('running');btn.textContent='▶ PRICE';if(data.error){document.getElementById('resultsArea').innerHTML='<div class="panel"><div class="panel-header"><div class="dot" style="background:var(--red)"></div> Error</div><div class="panel-body"><div class="log-area" style="color:var(--red)">'+data.error+'</div></div></div>';return}renderResults(data,bbg)}).catch(err=>{btn.disabled=false;btn.classList.remove('running');btn.textContent='▶ PRICE';document.getElementById('resultsArea').innerHTML='<div class="panel"><div class="panel-body"><div class="log-area" style="color:var(--red)">'+err+'</div></div></div>'})}
function renderResults(d,bbg){const g=d.greeks,mb=d.moneyness_bp,sa=document.getElementById('standalone_mode').checked,hB=bbg.npv>0;let ns=sa?'standalone':'';if(hB){const p=((d.npv-bbg.npv)/bbg.npv*100);ns=`${p>=0?'+':''}${fmt(p,4)}% vs BBG`}let h=`<div class="result-cards"><div class="rcard"><div class="label">NPV</div><div class="value match">${fmt(d.npv)}</div><div class="sub">${ns}</div></div><div class="rcard"><div class="label">σ total</div><div class="value">${fmt(d.sigma_total*10000,2)}</div><div class="sub">bp</div></div><div class="rcard"><div class="label">Yield Value</div><div class="value">${fmt(d.yield_value,3)}</div><div class="sub">bps</div></div><div class="rcard"><div class="label">ATM Rate</div><div class="value">${fmt(d.fair_rate*100,4)}%</div><div class="sub">Moneyness: ${mb>=0?'+':''}${fmt(mb,1)} bp</div></div><div class="rcard"><div class="label">Premium</div><div class="value">${fmt(d.premium_pct,4)}%</div><div class="sub">of notional</div></div><div class="rcard"><div class="label">Und. NPV</div><div class="value">${fmt(d.underlying_npv)}</div><div class="sub">${fmt(d.underlying_prem_pct,4)}%</div></div></div>`;let mi=`a = <span>${d.a_used}</span> ${d.a_calibrated?'(calibrated)':'(fixed)'} | σ_ATM = <span>${fmt(d.sigma_atm*10000,2)} bp</span>`;if(hB)mi+=` + Δσ = <span>${fmt(d.delta_spread*10000,2)} bp</span> → σ_total = <span>${fmt(d.sigma_total*10000,2)} bp</span>`;else mi+=` (standalone)`;h+=`<div class="panel"><div class="panel-header"><div class="dot"></div> Model</div><div class="model-bar">${mi}</div></div>`;if(hB){const np=((d.npv-bbg.npv)/bbg.npv*100),ab=(d.fair_rate-bbg.atm_strike/100)*10000,yD=d.yield_value-bbg.yield_value_bp,uD=d.underlying_prem_pct-bbg.underlying_premium,pD=d.premium_pct-bbg.premium;const vR=[['NPV (CAD)',fmt(bbg.npv),fmt(d.npv),fmt(np,4)+'%',diffClass(np)],['ATM Strike (%)',fmt(bbg.atm_strike,6),fmt(d.fair_rate*100,6),fmt(ab,2)+' bp',diffBpClass(ab,bbg.atm_strike*100)],['Yield Value (bp)',fmt(bbg.yield_value_bp,3),fmt(d.yield_value,3),fmt(yD,3)+' bp',diffBpClass(yD,bbg.yield_value_bp)],['Und. Premium (%)',fmt(bbg.underlying_premium,5),fmt(d.underlying_prem_pct,5),fmt(uD,5)+'%',diffBpClass(uD*100,bbg.underlying_premium)],['Premium (%)',fmt(bbg.premium,5),fmt(d.premium_pct,5),fmt(pD,5)+'%',diffClass(pD/bbg.premium*100)]].map(r=>`<tr><td class="name">${r[0]}</td><td class="bbg">${r[1]}</td><td class="val">${r[2]}</td><td class="diff ${r[4]}">${r[3]}</td></tr>`).join('');h+=`<div class="panel"><div class="panel-header"><div class="dot"></div> Valuation — BBG</div><div class="panel-body" style="padding:0"><table class="cmp-table"><thead><tr><th>Metric</th><th style="text-align:right">BBG</th><th style="text-align:right">QL</th><th style="text-align:right">Diff</th></tr></thead><tbody>${vR}</tbody></table></div></div>`}const gk=[{n:'DV01',q:g.dv01,b:hB?bbg.dv01:null},{n:'Gamma',q:g.gamma_1bp,b:hB?bbg.gamma_1bp:null},{n:'Vega',q:g.vega_1bp,b:hB?bbg.vega_1bp:null},{n:'Theta',q:g.theta_1d,b:hB?bbg.theta_1d:null},{n:'Delta',q:g.delta_hedge,b:null},{n:'Und. DV01',q:g.underlying_dv01,b:null}];if(hB){const gr=gk.map(x=>{const df=x.b!=null?x.q-x.b:null,pc=(x.b&&x.b!==0)?(df/Math.abs(x.b)*100):null,dc=pc!==null?diffClass(pc):'';return`<tr><td class="name">${x.n}</td><td class="bbg">${x.b!=null?fmt(x.b):'—'}</td><td class="val">${fmt(x.q)}</td><td class="diff ${dc}">${df!=null?(df>=0?'+':'')+fmt(df):'—'}</td><td class="diff ${dc}">${pc!=null?(pc>=0?'+':'')+fmt(pc,1)+'%':'—'}</td></tr>`}).join('');h+=`<div class="panel"><div class="panel-header"><div class="dot"></div> Greeks — BBG</div><div class="panel-body" style="padding:0"><table class="cmp-table"><thead><tr><th>Greek</th><th style="text-align:right">BBG</th><th style="text-align:right">QL</th><th style="text-align:right">Diff</th><th style="text-align:right">%</th></tr></thead><tbody>${gr}</tbody></table></div></div>`}else{const gr=gk.map(x=>`<tr><td class="name">${x.n}</td><td class="val" style="text-align:right">${fmt(x.q)}</td></tr>`).join('');h+=`<div class="panel"><div class="panel-header"><div class="dot"></div> Greeks</div><div class="panel-body" style="padding:0"><table class="cmp-table"><thead><tr><th>Greek</th><th style="text-align:right">Value</th></tr></thead><tbody>${gr}</tbody></table></div></div>`}h+=`<div class="panel"><div class="panel-header"><div class="dot"></div> Execution Log</div><div class="panel-body"><div class="log-area">${d.log||''}</div></div></div>`;h+=`<button class="btn-export" onclick="window.location.href='/api/export'">⬇ Export Excel</button> <button class="btn-export" style="margin-left:8px;border-color:var(--amber)" onclick="window.location.href='/api/export_pbi'">📊 Power BI</button>`;document.getElementById('resultsArea').innerHTML=h}
</script>
"""

CPG_BODY = r"""
<div style="display:flex;min-height:100vh;font-family:var(--sans);background:var(--bg)">
<!-- ═══ SIDEBAR PORTAIL ═══ -->
<aside style="width:248px;flex-shrink:0;background:linear-gradient(180deg,#1A1D21 0%,#16181C 50%,#121417 100%);position:fixed;top:0;left:0;height:100vh;z-index:100;display:flex;flex-direction:column;border-right:1px solid rgba(255,255,255,.04)">
<div style="padding:20px 20px 16px;border-bottom:1px solid rgba(255,255,255,.07)">
<img src="/static/d15-desjardins-logo-couleur.png" alt="Desjardins" style="height:28px;display:block;margin-bottom:8px">
<div style="font-size:10px;color:rgba(255,255,255,.35);letter-spacing:.8px;font-weight:600;text-transform:uppercase">Portail Trésorerie</div>
</div>
<div style="padding:20px;flex:1;overflow-y:auto">
<!-- Navigation portail -->
<div class="snav-sec">Outils</div>
<div class="snav active" id="nav-pricer" onclick="goPage('pricer')"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2l9 4.5v9L12 20l-9-4.5v-9L12 2z"/></svg>Pricer ÉT</div>
<div class="snav" id="nav-history" onclick="goPage('history')"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>Historique</div>

<div class="snav-sec" style="margin-top:24px">Pricers</div>
<div class="snav snav-active-mod"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/></svg>Épargne à terme prorogeable</div>
<div class="snav snav-disabled"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 12c2-4 4-4 6 0s4 4 6 0 4-4 6 0"/></svg>Bermudien <span class="bdg bdg-d" style="font-size:8px;padding:1px 5px;margin-left:4px">Bientôt</span></div>

<!-- Stepper (visible only on pricer page) -->
<div id="stepperArea" style="margin-top:24px">
<div class="snav-sec">Étapes du pricing</div>
<div class="ws" id="ws1" onclick="goStep(1)"><div class="ws-num" id="wn1">1</div><div><div class="ws-t">Données de marché</div><div class="ws-d">Courbe · Volatilité</div></div></div>
<div class="ws-line" id="wl1"></div>
<div class="ws" id="ws2" onclick="goStep(2)"><div class="ws-num" id="wn2">2</div><div><div class="ws-t">Instruments</div><div class="ws-d">Saisie · Fichier</div></div></div>
<div class="ws-line" id="wl2"></div>
<div class="ws" id="ws3" onclick="goStep(3)"><div class="ws-num" id="wn3">3</div><div><div class="ws-t">Résultats</div><div class="ws-d">Évaluation · Risques</div></div></div>
</div>
</div>
<div style="padding:14px 20px;border-top:1px solid rgba(255,255,255,.07);font-size:10px;color:rgba(255,255,255,.15)">Portail Trésorerie v3.0</div>
</aside>

<!-- ═══ CONTENU PRINCIPAL ═══ -->
<div style="margin-left:248px;flex:1;display:flex;flex-direction:column;min-height:100vh">
<!-- Barre supérieure -->
<div style="height:56px;background:rgba(255,255,255,.82);backdrop-filter:saturate(200%) blur(24px);-webkit-backdrop-filter:saturate(200%) blur(24px);border-bottom:1px solid rgba(0,0,0,.06);display:flex;align-items:center;justify-content:space-between;padding:0 28px;position:sticky;top:0;z-index:50">
<div style="display:flex;align-items:center;gap:10px"><img src="/static/d15-desjardins-logo-couleur.png" alt="" style="height:22px;opacity:.8"><span style="color:rgba(0,0,0,.1);font-size:18px;font-weight:200">|</span><span style="font-size:15px;font-weight:800;color:#1D1D1F;letter-spacing:-.3px" id="pageTitle">Pricer Épargne à terme</span><span style="color:#D1D1D6;margin:0 4px">›</span><span style="font-size:13px;font-weight:600;color:#636366" id="stepTitle">Données de marché</span></div>
<div style="display:flex;align-items:center;gap:12px">
<input type="date" id="evalDate" value="2026-02-26" style="font-family:var(--mono);font-size:11px;padding:6px 10px;border:1px solid rgba(0,0,0,.06);border-radius:8px;background:#fff;color:#1D1D1F;outline:none">
<span style="font-size:12px;color:#8E8E93;font-family:var(--mono)" id="clock"></span>
<div style="width:7px;height:7px;border-radius:50%;background:#00A463;box-shadow:0 0 0 2px #E9F5F0"></div>
</div></div>

<div style="flex:1;padding:28px;max-width:1360px;width:100%">

<!-- ═══════════ PAGE: PRICER ═══════════ -->
<div id="page-pricer">

<!-- STEP 1 -->
<div class="step" id="step1">
<div style="margin-bottom:24px"><h2 style="font-size:22px;font-weight:800;color:#1D1D1F;margin-bottom:4px">Données de marché</h2><p style="font-size:14px;color:#8E8E93">Charger la courbe de taux et la surface de volatilité.</p></div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px">
<div class="panel"><div class="panel-header"><div class="dot" style="background:var(--amber)"></div>Courbe CDF<span class="bdg bdg-d" style="margin-left:auto" id="curveBdg">Non chargée</span></div><div class="panel-body">
<div style="font-size:12px;color:var(--text3);margin-bottom:12px">CAD OIS CORRA + Spread CDF Desjardins</div>
<div class="wtabs" style="margin-bottom:12px"><button class="wtab active" onclick="setCrvTab('sql',this)">SQL Staging</button><button class="wtab" onclick="setCrvTab('csv',this)">Fichier CSV</button></div>
<div id="crvSql"><button class="btn-sec" style="width:100%" onclick="fetchCurveCDF()" id="btnFetch">⚡ Charger depuis QRM Staging</button></div>
<div id="crvCsv" style="display:none"><div class="upload-zone" onclick="document.getElementById('curveFile').click()" style="margin:0;min-height:70px"><div class="label">Téléverser CSV</div><div class="sublabel">termPoint, termType, TauxCDF</div><input type="file" id="curveFile" accept=".csv" onchange="loadCurveFile(this)"></div></div>
<div class="status" id="curveStatus"></div>
</div></div>
<div class="panel"><div class="panel-header"><div class="dot"></div>Surface de volatilité<span class="bdg bdg-a" style="margin-left:auto">Proxy</span></div><div class="panel-body">
<div style="font-size:12px;color:var(--text3);margin-bottom:12px">Vol normale swaption (pb). Proxy paramétrique par défaut.</div>
<div class="wtabs" style="margin-bottom:12px"><button class="wtab active" onclick="setVolTab('proxy',this)">Proxy</button><button class="wtab" onclick="setVolTab('file',this)">Fichier</button></div>
<div id="volProxy"><div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px">
<div class="pinp-wrap"><label>Base (pb)</label><input type="number" id="volBase" value="65" step="5" class="pinp" style="width:100%"></div>
<div class="pinp-wrap"><label>Pente</label><input type="number" id="volSlope" value="-2" step="0.5" class="pinp" style="width:100%"></div>
<div class="pinp-wrap"><label>Plancher</label><input type="number" id="volFloor" value="30" step="5" class="pinp" style="width:100%"></div>
</div><button class="btn-sec" style="width:100%;margin-top:10px" onclick="applyVolProxy()">Générer proxy</button></div>
<div id="volFile" style="display:none"><div class="upload-zone" onclick="document.getElementById('volFileI').click()" style="margin:0;min-height:70px"><div class="label">Téléverser la surface</div><div class="sublabel">Excel/CSV : expiry × tenor</div><input type="file" id="volFileI" accept=".csv,.xlsx" onchange="loadVolFile(this)"></div></div>
<div class="status" id="volStatus"></div>
<div id="volPreview" style="margin-top:12px;max-height:220px;overflow:auto;display:none"></div>
</div></div>
</div>
<div class="panel" id="curveChartP" style="display:none"><div class="panel-header"><div class="dot"></div>Aperçu de la courbe<span class="bdg bdg-g" id="curvePts" style="margin-left:auto"></span></div><div class="panel-body" style="padding:8px"><canvas id="curveCanvas" height="180" style="width:100%"></canvas></div></div>
<div class="panel" id="curveTableP" style="margin-top:16px;display:none"><div class="panel-header"><div class="dot"></div>Détail des points</div><div class="panel-body" style="padding:0;overflow-x:auto" id="curvePreview"></div></div>
<div style="display:flex;justify-content:flex-end;margin-top:24px">
<button class="btn" onclick="goStep(2)" style="padding:12px 32px">Suivant → Instruments</button>
</div>
</div>

<!-- STEP 2 -->
<div class="step" id="step2" style="display:none">
<div style="margin-bottom:24px"><h2 style="font-size:22px;font-weight:800;color:#1D1D1F;margin-bottom:4px">Instruments d'épargne à terme</h2><p style="font-size:14px;color:#8E8E93">Saisir les paramètres ou charger un fichier Excel.</p></div>
<div class="wtabs" id="dealTabs"><button class="wtab active" onclick="setDealTab('manual',this)">Saisie manuelle</button><button class="wtab" onclick="setDealTab('upload',this)">Téléverser Excel</button><button class="wtab" onclick="setDealTab('portfolio',this)">Portefeuille existant</button></div>
<!-- Saisie manuelle -->
<div id="dealManual">
<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
<div class="panel"><div class="panel-header"><div class="dot" style="background:var(--amber)"></div>Paramètres</div><div class="panel-body">
<div class="prow"><div><div class="plbl">Type</div></div><select id="extType" onchange="extTypeChanged()" style="width:180px;font-family:var(--mono);font-size:12px;padding:7px 10px;border:1px solid rgba(0,0,0,.06);border-radius:8px;background:#fff"><option value="COUPON">COUPON (coupons annuels)</option><option value="LINEAR ACCRUAL">ACCUMULATION LINÉAIRE</option></select></div>
<div class="prow"><div><div class="plbl">FundServ</div></div><input type="text" id="extFund" value="" placeholder="Ex: ABC12345" class="pinp" style="width:120px;color:var(--text)"></div>
<div class="prow"><div><div class="plbl">Nominal ($)</div></div><input type="number" id="extNot" value="10000" step="1000" class="pinp" style="width:120px;color:var(--text)"></div>
<div class="prow"><div><div class="plbl">Taux client (%)</div></div><input type="number" id="extRate" value="4.10" step="0.05" class="pinp"></div>
<div class="prow"><div><div class="plbl">Fréquence</div></div><input type="number" id="extFreq" value="1" min="0" max="12" class="pinp" style="width:60px;color:var(--text)"></div>
</div></div>
<div class="panel"><div class="panel-header"><div class="dot"></div>Dates</div><div class="panel-body">
<div class="prow"><div><div class="plbl">Émission</div></div><input type="date" id="extEmission" value="2025-10-02" class="date-inp"></div>
<div class="prow"><div><div class="plbl">Échéance initiale</div><div class="pdsc">1ère maturité possible</div></div><input type="date" id="extInitMat" value="2026-10-02" class="date-inp"></div>
<div class="prow"><div><div class="plbl">Échéance finale</div><div class="pdsc">Max si toujours prorogé</div></div><input type="date" id="extFinalMat" value="2035-10-02" class="date-inp"></div>
<div style="margin-top:16px;padding:10px 14px;background:#E9F5F0;border-radius:8px;border:1px solid #CCE7DC;font-size:11px;color:#006F40;line-height:1.5" id="dealPreview"></div>
</div></div>
</div></div>
<!-- Téléverser Excel -->
<div id="dealUpload" style="display:none">
<div class="panel"><div class="panel-header"><div class="dot"></div>Charger un fichier de épargnes à terme prorogeables</div><div class="panel-body">
<div class="upload-zone" onclick="document.getElementById('extFile').click()" style="margin:0;padding:32px"><div style="font-size:28px;margin-bottom:8px">📄</div><div class="label">Déposer un fichier .xlsx / .csv</div><div class="sublabel">CodeTransaction, FundServ, Montant, Coupon, DateEmission, etc.</div><input type="file" id="extFile" accept=".xlsx,.xls,.csv" onchange="loadExtFile(this)"></div>
<div style="display:flex;gap:12px;margin-top:10px;font-size:11px"><a href="/cpg/api/download_ext_template" style="color:var(--accent);text-decoration:none">⬇ Modèle Excel</a><button onclick="populateFormFromFile()" class="btn-sec" style="font-size:11px;padding:4px 10px;display:none" id="btnPopulate">✏ Modifier dans le formulaire</button></div>
<div class="status" id="extFileStatus"></div>
<div id="extFilePreview" style="margin-top:12px"></div>
</div></div></div>
<!-- Portefeuille existant -->
<div id="dealPortfolio" style="display:none">
<div class="panel"><div class="panel-header"><div class="dot"></div>Portefeuille de trades</div><div class="panel-body">
<div class="upload-zone" onclick="document.getElementById('tradesFile').click()" style="margin:0"><div style="font-size:28px;margin-bottom:8px">📊</div><div class="label">Charger les transactions (.xlsx / .csv)</div><input type="file" id="tradesFile" accept=".xlsx,.xls,.csv" onchange="loadTradesFile(this)"></div>
<div style="font-size:11px;color:var(--text3);margin-top:8px"><a href="/cpg/api/download_trades_template" style="color:var(--accent);text-decoration:none">⬇ Modèle Excel</a></div>
<div class="status" id="tradesStatus"></div>
</div></div></div>
<div style="display:flex;justify-content:space-between;margin-top:24px">
<button class="btn-sec" onclick="goStep(1)">← Données de marché</button>
<button class="btn" id="btnPrice" onclick="runAllPricing()" style="padding:12px 32px">▶ Lancer le pricing</button>
</div>
</div>

<!-- STEP 3 -->
<div class="step" id="step3" style="display:none">
<div style="margin-bottom:16px;display:flex;justify-content:space-between;align-items:flex-start">
<div><h2 style="font-size:22px;font-weight:800;color:#1D1D1F;margin-bottom:4px">Résultats</h2><p style="font-size:14px;color:#8E8E93" id="resSubtitle">Évaluation · Risques · Analyse</p></div>
<div style="display:flex;gap:8px">
<button class="btn-sec" onclick="goStep(2)">← Modifier</button>
<button class="btn-sec" onclick="exportResults()">⬇ Exporter</button>
</div>
</div>
<div class="rtabs" id="resTabs">
<button class="rtab active" onclick="showRes('summary',this)">Synthèse</button>
<button class="rtab" onclick="showRes('greeks',this)">Greeks</button>
<button class="rtab" onclick="showRes('schedule',this)">Exercice</button>
<button class="rtab" onclick="showRes('cashflows',this)">Flux</button>
<button class="rtab" onclick="showRes('curve',this)">Courbe</button>
<button class="rtab" onclick="showRes('calib',this)">Modèle</button>
<button class="rtab" onclick="showRes('vol',this)">Volatilité</button>
<button class="rtab" onclick="showRes('krr',this)">Risque clé</button>
<button class="rtab" onclick="showRes('scenarios',this)">Scénarios</button>
</div>
<div id="resPanel-summary"></div>
<div id="resPanel-greeks" style="display:none"></div>
<div id="resPanel-schedule" style="display:none"></div>
<div id="resPanel-cashflows" style="display:none"></div>
<div id="resPanel-curve" style="display:none"></div>
<div id="resPanel-calib" style="display:none"></div>
<div id="resPanel-vol" style="display:none"></div>
<div id="resPanel-krr" style="display:none"></div>
<div id="resPanel-scenarios" style="display:none"></div>
</div>
</div><!-- /page-pricer -->

<!-- ═══════════ PAGE: HISTORIQUE ═══════════ -->
<div id="page-history" style="display:none">
<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:24px">
<div><h2 style="font-size:22px;font-weight:800;color:#1D1D1F;margin-bottom:4px">Historique des opérations</h2><p style="font-size:14px;color:#8E8E93">Consultez, rechargez ou supprimez vos sessions de pricing précédentes.</p></div>
<div style="display:flex;gap:8px">
<button class="btn-sec" onclick="clearHistory()" id="btnClearHist">🗑 Tout supprimer</button>
<button class="btn-sec" onclick="exportHistory()">⬇ Exporter CSV</button>
</div>
</div>
<div class="panel" id="histPanel"><div class="panel-header"><div class="dot" style="background:#8E8E93"></div>Sessions enregistrées<span class="bdg bdg-d" style="margin-left:auto" id="histCount">0 sessions</span></div>
<div class="panel-body" style="padding:0;max-height:70vh;overflow-y:auto" id="histBody"><div style="padding:40px;color:#8E8E93;font-size:13px;text-align:center">Aucune session enregistrée.<br><span style="font-size:12px">Les résultats de pricing seront automatiquement sauvegardés ici.</span></div></div></div>
</div><!-- /page-history -->

</div><!-- /padding -->
<footer style="padding:16px 28px;border-top:1px solid rgba(0,0,0,.04);display:flex;justify-content:space-between;align-items:center;font-size:11px;color:#C7C7CC;background:rgba(255,255,255,.5)"><span>Portail Trésorerie Desjardins — Épargne à terme</span><span style="font-family:var(--mono);font-size:10px" id="footEval"></span></footer>
</div></div>

<link rel="stylesheet" href="/static/style.css">

<script src="/static/app.js"></script>

"""

# ═══════════════════════════════════════════════════════════════════════════
#  PAGE ASSEMBLY — each tool is fully self-contained
# ═══════════════════════════════════════════════════════════════════════════

def _page(title, active, body, extra_right=""):
    """Assemble a complete HTML page from shared head + header + body."""
    return (
        f"<!DOCTYPE html><html lang='fr'><head>{SHARED_HEAD}"
        f"<title>{title}</title></head><body>"
        f"{_header_html(active, extra_right)}"
        f"{body}</body></html>"
    )


# ═══════════════════════════════════════════════════════════════════════════
#  ROUTES — BERMUDAN SWAPTION PRICER
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    if not SHOW_BERMUDAN:
        return redirect("/cpg", code=302)
    badge = '<div style="font-size:11px;font-family:var(--mono);padding:5px 14px;border-radius:99px;background:var(--green-bg);color:var(--green);border:1px solid rgba(0,135,78,0.15);font-weight:600" id="statusBadge">READY</div>'
    return _page("Bermudan Swaption Pricer — Desjardins", "bermudan", BERMUDAN_BODY, badge)


@app.route("/api/upload_excel", methods=["POST"])
def api_upload_excel():
    if not SHOW_BERMUDAN:
        return jsonify({"error": "Module Bermudan désactivé"}), 404
    try:
        f = request.files.get("file")
        if not f:
            return jsonify({"error": "No file uploaded"})
        tmp = os.path.join(tempfile.gettempdir(), "mkt_data.xlsx")
        f.save(tmp)
        wb = openpyxl.load_workbook(tmp, data_only=True)
        result = {}
        # Curve sheet
        curve_sheet = None
        for name in wb.sheetnames:
            if "curve" in name.lower() or "ois" in name.lower():
                curve_sheet = name
                break
        if not curve_sheet:
            curve_sheet = wb.sheetnames[0]
        ws = wb[curve_sheet]
        curve_data = []
        # Auto-detect columns
        header = [str(c.value or "").strip().lower() for c in ws[1]]
        date_col = 0
        df_col = 1
        for i, h in enumerate(header):
            if "date" in h:
                date_col = i
            if "discount" in h or h == "df":
                df_col = i
        all_rows = list(ws.iter_rows(min_row=2, values_only=True))
        if all_rows and df_col == 1:
            try:
                test_val = float(all_rows[0][1])
                if test_val > 1.0:
                    for ci in range(len(all_rows[0])):
                        try:
                            tv = float(all_rows[0][ci])
                            if 0 < tv < 1.0:
                                df_col = ci
                                break
                        except:
                            pass
            except:
                pass
        for row in all_rows:
            if row[date_col] is None:
                continue
            d = row[date_col]
            d = d.strftime("%Y-%m-%d") if isinstance(d, datetime) else str(d).strip().split()[0]
            try:
                curve_data.append([d, float(row[df_col])])
            except:
                continue
        result["curve"] = curve_data
        # Vol sheet
        vol_sheet = None
        for name in wb.sheetnames:
            if "vol" in name.lower() or "bvol" in name.lower():
                vol_sheet = name
                break
        if not vol_sheet and len(wb.sheetnames) > 1:
            vol_sheet = wb.sheetnames[1]
        if vol_sheet:
            ws = wb[vol_sheet]
            rows = list(ws.iter_rows(values_only=True))
            tenor_labels = [str(c).strip() for c in rows[0][1:] if c is not None]
            expiry_labels = []
            vol_values = []
            for row in rows[1:]:
                if row[0] is None:
                    continue
                expiry_labels.append(str(row[0]).strip())
                vol_values.append([float(c) if c else 0.0 for c in row[1:1+len(tenor_labels)]])
            result["vol_values"] = vol_values
            result["expiry_labels"] = expiry_labels
            result["tenor_labels"] = tenor_labels
        wb.close()
        return jsonify(result)
    except Exception as e:
        import traceback
        return jsonify({"error": f"{e}\n{traceback.format_exc()}"})


@app.route("/api/price", methods=["POST"])
def api_price():
    try:
        cfg = request.json
        vol_values = np.array(cfg.get("vol_surface_data", {}).get("values", []), dtype=float)
        from bbg_fetcher import labels_to_years, EXPIRY_LABEL_TO_YEARS, TENOR_LABEL_TO_YEARS
        exp_labels = cfg.get("vol_surface_data", {}).get("expiry_labels", [])
        tnr_labels = cfg.get("vol_surface_data", {}).get("tenor_labels", [])
        market_data = {
            "curve": cfg.get("curve_data", []),
            "vol_surface": vol_values,
            "expiry_grid": labels_to_years(exp_labels, EXPIRY_LABEL_TO_YEARS),
            "tenor_grid": labels_to_years(tnr_labels, TENOR_LABEL_TO_YEARS),
            "bbg_npv": float(cfg.get("benchmark", {}).get("npv", 0)),
        }
        log_buf = io.StringIO()
        with redirect_stdout(log_buf):
            from pricer import BermudanPricer
            pricer = BermudanPricer(cfg, market_data)
            pricer.setup()
            pricer.calibrate()
            pricer.compute_greeks()
        bps_leg = abs(float(pricer.swap.fixedLegBPS()))
        yv = pricer.npv / bps_leg if bps_leg else 0
        app.config["LAST_PRICER"] = pricer
        app.config["LAST_CFG"] = cfg
        return jsonify({
            "npv": pricer.npv, "sigma_atm": pricer.sigma_atm,
            "sigma_total": pricer.sigma_total, "delta_spread": pricer.delta_spread,
            "fair_rate": pricer.fair_rate, "underlying_npv": pricer.underlying_npv,
            "yield_value": yv, "premium_pct": pricer.npv / pricer.notional * 100,
            "underlying_prem_pct": pricer.underlying_npv / pricer.notional * 100,
            "moneyness_bp": (pricer.strike - pricer.fair_rate) * 10000,
            "greeks": pricer.greeks,
            "a_used": pricer.a, "a_calibrated": pricer.calib_a,
            "log": log_buf.getvalue(),
        })
    except Exception as e:
        import traceback
        return jsonify({"error": f"{e}\n\n{traceback.format_exc()}"})


@app.route("/api/export")
def api_export():
    pricer = app.config.get("LAST_PRICER")
    if not pricer:
        return "No results. Run pricer first.", 400
    xlsx = os.path.join(tempfile.gettempdir(), "bermudan_results.xlsx")
    pricer.export_excel(xlsx)
    return send_file(xlsx, as_attachment=True, download_name="bermudan_results.xlsx")


@app.route("/api/export_pbi")
def api_export_pbi():
    """Export structured Excel optimized for Power BI."""
    pricer = app.config.get("LAST_PRICER")
    cfg = app.config.get("LAST_CFG")
    if not pricer:
        return "No results. Run pricer first.", 400
    try:
        from run_and_export import export_pbi_excel
        xlsx = os.path.join(tempfile.gettempdir(), "pbi_data.xlsx")
        export_pbi_excel(pricer, cfg, xlsx)
        return send_file(xlsx, as_attachment=True, download_name="pbi_data.xlsx")
    except Exception as e:
        import traceback
        return str(e) + "\n" + traceback.format_exc(), 500


# ═══════════════════════════════════════════════════════════════════════════
#  ROUTES — CPG PORTFOLIO PRICER (fully isolated from Bermudan)
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/cpg")
def cpg_index():
    extra_css = "" if SHOW_BERMUDAN else "<style>aside a[href='/']{display:none!important}</style>"
    return (
        f"<!DOCTYPE html><html lang='fr'><head>{SHARED_HEAD}{extra_css}"
        f"<title>Épargne à terme — Portail Trésorerie Desjardins</title></head><body style='margin:0;padding:0'>"
        f"{CPG_BODY}</body></html>"
    )


@app.route("/cpg/api/upload_curve", methods=["POST"])
def cpg_upload_curve():
    try:
        f = request.files.get("file")
        if not f:
            return jsonify({"error": "Aucun fichier"})
        import pandas as pd
        tmp = os.path.join(tempfile.gettempdir(), "cpg_curve.csv")
        f.save(tmp)
        from cpg.curve_sql import load_curve_from_csv
        df = load_curve_from_csv(tmp)
        app.config["CPG_CURVE"] = df
        app.config.pop("_FULL_RESULTS_CACHE", None)
        rng = f"{df['ApproxDays'].min()}j – {df['ApproxDays'].max()}j"
        # Build preview rows
        preview = []
        for _, r in df.iterrows():
            preview.append({
                "termPoint": int(r["termPoint"]),
                "termType": r["termType"],
                "ZeroCouponSpreadCDF": round(float(r["ZeroCouponSpreadCDF"]), 6) if "ZeroCouponSpreadCDF" in r and pd.notna(r.get("ZeroCouponSpreadCDF")) else None,
                "ZeroCouponBase": round(float(r["ZeroCouponBase"]), 6) if "ZeroCouponBase" in r and pd.notna(r.get("ZeroCouponBase")) else None,
                "TauxCDF": round(float(r["TauxCDF"]), 6),
                "ApproxDays": int(r["ApproxDays"]),
            })
        return jsonify({"points": len(df), "range": rng, "preview": preview})
    except Exception as e:
        return jsonify({"error": str(e)})


# ─────────────────────────────────────────────────────────────────────────────
# CPG — Fetch CAD CDF (spread) + CAD OIS CORRA (base) depuis QRM_STAGING.QUOT
# Sortie normalisée: termPoint, termType, ZeroCouponSpreadCDF, ZeroCouponBase,
#                    TauxCDF (= Base + Spread), ApproxDays
# ─────────────────────────────────────────────────────────────────────────────


@app.route("/cpg/api/fetch_curve_cdf", methods=["POST"])
def cpg_fetch_curve_cdf():
    """
    Récupère la courbe CDF en combinant:
      - A: CAD CDF (ZeroCoupon -> spread)
      - B: CAD OIS CORRA (ZeroCoupon -> base)
    au dernier EvaluationDate disponible <= eval_date (si fourni), sinon dernier disponible.
    Source: [BD_ET_QRM_Staging].[dbo].[QRM_MUREX_YIELD_CURVE_QUOT]
    """
    try:
        import pandas as pd
        import numpy as np
        from datetime import datetime, date

        # Check dependencies first
        try:
            import pyodbc
        except ImportError:
            return jsonify({
                "error": "Module 'pyodbc' non installé.\n\n"
                         "Pour activer la connexion SQL, exécuter dans le venv :\n"
                         "  pip install SQLAlchemy pyodbc\n\n"
                         "Note : nécessite aussi 'ODBC Driver 17 for SQL Server' "
                         "(installé par défaut sur les postes Desjardins).\n\n"
                         "Alternative : charger la courbe via fichier CSV."
            }), 400

        data = request.json or {}
        eval_date_str = (data.get("eval_date") or "").strip()

        cutoff = None
        if eval_date_str:
            try:
                cutoff = datetime.strptime(eval_date_str, "%Y-%m-%d").date()
            except ValueError:
                return jsonify({"error": "Format de 'eval_date' invalide (attendu YYYY-MM-DD)."}), 400

        server   = "MSSQL-DOT.Desjardins.com"
        database = "BD_ET_QRM_Staging"
        engine = create_engine(
            f"mssql+pyodbc://@{server}/{database}"
            f"?driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes"
        )

        sql_with_cutoff = text("""
WITH latest AS (
    SELECT MAX(EvaluationDate) AS EvaluationDate
    FROM [BD_ET_QRM_Staging].[dbo].[QRM_MUREX_YIELD_CURVE_QUOT]
    WHERE EvaluationDate <= :cutoff
      AND CurveLabel IN ('CAD CDF','CAD OIS CORRA')
)
SELECT
    A.EvaluationDate, A.CurveLabel, A.termPoint, A.termType,
    A.ZeroCoupon AS ZeroCouponSpreadCDF,
    B.ZeroCoupon AS ZeroCouponBase,
    (A.ZeroCoupon + B.ZeroCoupon) AS TauxCDF,
    A.NbrJoursQRM
FROM [BD_ET_QRM_Staging].[dbo].[QRM_MUREX_YIELD_CURVE_QUOT] AS A
LEFT JOIN [BD_ET_QRM_Staging].[dbo].[QRM_MUREX_YIELD_CURVE_QUOT] AS B
    ON  B.CurveLabel     = 'CAD OIS CORRA'
    AND B.termPoint      = A.termPoint
    AND B.termType       = A.termType
    AND B.EvaluationDate = (SELECT EvaluationDate FROM latest)
WHERE A.CurveLabel     = 'CAD CDF'
  AND A.EvaluationDate = (SELECT EvaluationDate FROM latest)
ORDER BY A.NbrJoursQRM;
""")

        sql_no_cutoff = text("""
WITH latest AS (
    SELECT MAX(EvaluationDate) AS EvaluationDate
    FROM [BD_ET_QRM_Staging].[dbo].[QRM_MUREX_YIELD_CURVE_QUOT]
    WHERE CurveLabel IN ('CAD CDF','CAD OIS CORRA')
)
SELECT
    A.EvaluationDate, A.CurveLabel, A.termPoint, A.termType,
    A.ZeroCoupon AS ZeroCouponSpreadCDF,
    B.ZeroCoupon AS ZeroCouponBase,
    (A.ZeroCoupon + B.ZeroCoupon) AS TauxCDF,
    A.NbrJoursQRM
FROM [BD_ET_QRM_Staging].[dbo].[QRM_MUREX_YIELD_CURVE_QUOT] AS A
LEFT JOIN [BD_ET_QRM_Staging].[dbo].[QRM_MUREX_YIELD_CURVE_QUOT] AS B
    ON  B.CurveLabel     = 'CAD OIS CORRA'
    AND B.termPoint      = A.termPoint
    AND B.termType       = A.termType
    AND B.EvaluationDate = (SELECT EvaluationDate FROM latest)
WHERE A.CurveLabel     = 'CAD CDF'
  AND A.EvaluationDate = (SELECT EvaluationDate FROM latest)
ORDER BY A.NbrJoursQRM;
""")

        try:
            with engine.begin() as conn:
                if cutoff:
                    df = pd.read_sql_query(sql_with_cutoff, conn, params={"cutoff": cutoff})
                else:
                    df = pd.read_sql_query(sql_no_cutoff, conn)
        except Exception as conn_err:
            err_str = str(conn_err).lower()
            if "login" in err_str or "trusted" in err_str or "authentication" in err_str:
                msg = "Authentification SQL échouée. Vérifiez que votre compte Windows a accès à BD_ET_QRM_Staging."
            elif "network" in err_str or "server" in err_str or "connect" in err_str or "timeout" in err_str:
                msg = "Serveur SQL inaccessible (MSSQL-DOT.Desjardins.com). Vérifiez que vous êtes connecté au réseau Desjardins (VPN si en télétravail)."
            elif "driver" in err_str or "odbc" in err_str:
                msg = "ODBC Driver 17 for SQL Server non trouvé. Contacter le support TI pour l'installation."
            else:
                msg = f"Erreur de connexion SQL : {conn_err}"
            return jsonify({"error": msg}), 500

        if df.empty:
            msg = f"Aucune donnée trouvée (<= {cutoff})" if cutoff else "Aucune donnée trouvée (aucune date disponible)"
            return jsonify({"error": msg}), 404

        out = pd.DataFrame({
            "termPoint":           df["termPoint"].astype(int),
            "termType":            df["termType"].astype(str),
            "ZeroCouponSpreadCDF": df["ZeroCouponSpreadCDF"].astype(float),
            "ZeroCouponBase":      df["ZeroCouponBase"].astype(float),
            "TauxCDF":             df["TauxCDF"].astype(float),
            "ApproxDays":          (df["NbrJoursQRM"] if "NbrJoursQRM" in df else np.nan),
        })

        if out["ApproxDays"].isna().any():
            factor = {"Day": 1, "Week": 7, "Month": 30, "Year": 365,
                      "Jour": 1, "Semaine": 7, "Mois": 30, "Année": 365, "An": 365}
            out["ApproxDays"] = out.apply(
                lambda r: int(r["termPoint"]) * factor.get(str(r["termType"]).strip(), 30),
                axis=1
            ).astype(int)
        else:
            out["ApproxDays"] = out["ApproxDays"].astype(int)

        out = out.sort_values("ApproxDays").reset_index(drop=True)

        used_eval = df["EvaluationDate"].iloc[0]
        used_eval_str = used_eval.strftime("%Y-%m-%d") if hasattr(used_eval, "strftime") else str(used_eval)
        app.config["CPG_CURVE"] = out
        app.config.pop("_FULL_RESULTS_CACHE", None)

        rng = f"{int(out['ApproxDays'].min())}j \u2013 {int(out['ApproxDays'].max())}j"
        preview = [{
            "termPoint": int(r.termPoint),
            "termType": str(r.termType),
            "ZeroCouponSpreadCDF": round(float(r.ZeroCouponSpreadCDF), 6),
            "ZeroCouponBase": round(float(r.ZeroCouponBase), 6),
            "TauxCDF": round(float(r.TauxCDF), 6),
            "ApproxDays": int(r.ApproxDays),
        } for _, r in out.iterrows()]

        return jsonify({
            "points": int(len(out)),
            "range": rng,
            "preview": preview,
            "EvaluationDate": used_eval_str
        })

    except Exception as e:
        import traceback
        return jsonify({"error": f"{e}\n{traceback.format_exc()}"}), 500



@app.route("/cpg/api/upload_trades", methods=["POST"])
def cpg_upload_trades():
    try:
        f = request.files.get("file")
        if not f:
            return jsonify({"error": "Aucun fichier"})
        ext = os.path.splitext(f.filename)[1].lower()
        tmp = os.path.join(tempfile.gettempdir(), "cpg_trades" + ext)
        f.save(tmp)
        from cpg.trades import load_trades_file
        df = load_trades_file(tmp)
        app.config["CPG_TRADES"] = df
        types = ", ".join(f"{k}:{v}" for k, v in df["CodeTransaction"].value_counts().items())
        return jsonify({"count": len(df), "types": types})
    except Exception as e:
        return jsonify({"error": str(e)})



@app.route("/cpg/api/download_trades_template")
def cpg_download_trades_template():
    import pandas as pd
    cols = ["CodeTransaction","Inventaire","Contrepartie","DateÉmission","DateEcheanceInitial",
            "DateEcheanceFinal","Montant","Coupon","Marge","Frequence","BaseCalcul",
            "Devise","CUSIP","FundServ"]
    sample = [
        ["COUPON","PRORO","418414","2025-12-19","2026-12-19","2035-12-19"," 1,000.00  $ ","5.00%","0.00%","Annuel","ACT/365","CAD","CA31430XKX98","DSN12345"],
        ["COUPON","PRORO","418414","2025-11-19","2026-11-19","2035-11-19"," 1,000.00  $ ","4.50%","0.00%","Annuel","ACT/365","CAD","CA31430XKK77","DSN12345"],
        ["LINEAR ACCRUAL","PRORO","418414","2025-12-19","2040-12-19","2040-12-19"," 1,000.00  $ ","6.00%","0.00%","Maturité","ACT/365","CAD","CA31393ZGQ96","DSN12345"],
    ]
    df = pd.DataFrame(sample, columns=cols)
    tmp = os.path.join(tempfile.gettempdir(), "trades_template.xlsx")
    df.to_excel(tmp, index=False, engine="openpyxl")
    return send_file(tmp, as_attachment=True, download_name="trades_template.xlsx")


@app.route("/cpg/api/price", methods=["POST"])
@require_curve_and_trades
def cpg_price():
    try:
        import pandas as pd
        curve_df = app.config["CPG_CURVE"]
        trades_df = app.config["CPG_TRADES"]
        eval_date = request.json.get("eval_date", "2026-02-26")
        from cpg.pricing import price_cpg_portfolio
        results = price_cpg_portfolio(trades_df, curve_df, eval_date)
        app.config["CPG_RESULTS"] = results
        app.config["CPG_LAST_EXPORT"] = {"type": "portfolio", "data": results}
        ok = results[results["Status"] == "OK"]
        # Serialize dates for JSON
        rows = results.copy()
        for c in ["DateEmission", "DateEcheanceFinal"]:
            if c in rows.columns:
                rows[c] = rows[c].apply(lambda x: x.strftime("%Y-%m-%d") if hasattr(x, "strftime") else str(x))
        return jsonify({
            "count_total": len(results),
            "count_ok": len(ok),
            "pv_total": round(ok["PV"].sum(), 2),
            "notional_total": round(ok["Montant"].sum(), 2),
            "avg_duration": round((ok["Duration_Approx"] * ok["PV"]).sum() / ok["PV"].sum(), 4) if ok["PV"].sum() > 0 else 0,
            "results": rows.drop(columns=["Cashflows"], errors="ignore").to_dict(orient="records"),
        })
    except Exception as e:
        import traceback
        return jsonify({"error": f"{e}\n{traceback.format_exc()}"})


@app.route("/cpg/api/export")
def cpg_export():
    """Export last pricing results to multi-sheet Excel."""
    import tempfile
    last = app.config.get("CPG_LAST_EXPORT")
    if last is None:
        return "Aucun résultat à exporter. Lancez le pricing d'abord.", 400

    tmp = os.path.join(tempfile.gettempdir(), "resultats_epargne_terme.xlsx")

    try:
        if last["type"] == "portfolio":
            from cpg.export import export_results
            export_results(last["data"], tmp)
        else:
            _export_extendible(last["data"], tmp)

        return send_file(tmp, as_attachment=True,
                         download_name=f"resultats_epargne_terme_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx")
    except Exception as e:
        import traceback
        return f"Erreur d'export: {e}\n{traceback.format_exc()}", 500


def _export_extendible(data, path):
    """Export extendible / full_results bundle to multi-sheet Excel."""
    import pandas as pd

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        # Sheet 1: Sommaire
        ds = data.get("deal_summary", data)
        val = data.get("valuation", {})
        prem = data.get("premium", {})
        summary_rows = []
        if val:
            for k, v in [
                ("VAN", val.get("npv")), ("VAN sans frais", val.get("npv_without_fee")),
                ("VA fixe", val.get("pv_fixed")), ("Valeur option", val.get("option_value")),
                ("Méthode option", val.get("option_method")),
                ("Taux client (%)", val.get("atm_strike")),
                ("VAN/Nominal (%)", val.get("pv_notional_ratio")),
                ("Valeur intrinsèque", val.get("intrinsic_value")),
                ("Prime option ($)", prem.get("option_premium")),
                ("Prime option (%)", prem.get("option_premium_pct")),
                ("Prime sous-jacent ($)", prem.get("underlying_premium")),
                ("Total ($)", prem.get("total_premium")),
            ]:
                summary_rows.append({"Métrique": k, "Valeur": v})
        else:
            for k in ["PV_total", "PV_fixed", "option_value", "client_rate_pct", "cpg_type", "FundServ"]:
                if k in data:
                    summary_rows.append({"Métrique": k, "Valeur": data[k]})
        pd.DataFrame(summary_rows).to_excel(writer, sheet_name="Sommaire", index=False)

        # Sheet 2: Fiche instrument
        if isinstance(ds, dict) and "style" in ds:
            fiche = [{"Champ": k, "Valeur": v} for k, v in ds.items()]
            pd.DataFrame(fiche).to_excel(writer, sheet_name="Instrument", index=False)

        # Sheet 3: Calendrier d'exercice
        exs = data.get("exercise_analysis", [])
        if not exs:
            sc = data.get("schedule", {})
            exs = sc.get("rows", [])
        if exs:
            pd.DataFrame(exs).to_excel(writer, sheet_name="Exercice", index=False)

        # Sheet 4: Flux de trésorerie
        cf = data.get("cashflows", {})
        cf_rows = cf.get("rows", [])
        if cf_rows:
            pd.DataFrame(cf_rows).to_excel(writer, sheet_name="Flux", index=False)

        # Sheet 5: Courbe
        cu = data.get("curve_summary", {})
        cu_pts = cu.get("points", cu.get("rows", []))
        if cu_pts:
            pd.DataFrame(cu_pts).to_excel(writer, sheet_name="Courbe", index=False)

        # Sheet 6: Greeks
        g = data.get("greeks", {})
        if g and g.get("available"):
            gk_rows = [{"Greek": k, "Valeur": v} for k, v in g.items()
                       if isinstance(v, (int, float)) and k != "available"]
            if gk_rows:
                pd.DataFrame(gk_rows).to_excel(writer, sheet_name="Greeks", index=False)

        # Sheet 7: Scénarios
        scens = data.get("scenarios", [])
        if scens:
            pd.DataFrame(scens).to_excel(writer, sheet_name="Scénarios", index=False)

        # Sheet 8: Barème de remboursement
        remb = data.get("remboursement_schedule", [])
        if remb:
            pd.DataFrame(remb).to_excel(writer, sheet_name="Remboursement", index=False)

        # Auto-adjust column widths
        for sheet_name in writer.sheets:
            ws = writer.sheets[sheet_name]
            for col in ws.iter_cols(min_row=1, max_row=1):
                max_len = max(len(str(cell.value or "")) for cell in ws[col[0].column_letter])
                ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 35)


# ═══════════════════════════════════════════════════════════════════════════
#  ROUTES — CPG Risk Analytics (Greeks, Vol, Scenarios)
# ═══════════════════════════════════════════════════════════════════════════


@app.route("/cpg/api/greeks", methods=["POST"])
@require_curve_and_trades
def cpg_greeks():
    """Compute full risk analytics: DV01, Gamma, KR-DV01, Theta, Vega, Scenarios."""
    try:
        curve_df = app.config["CPG_CURVE"]
        trades_df = app.config["CPG_TRADES"]

        data = request.json or {}
        eval_date = data.get("eval_date", "2026-02-26")
        bump_bp = float(data.get("bump_bp", 1.0))

        from cpg.greeks import compute_all_greeks
        vol_connector = app.config.get("CPG_VOL_CONNECTOR")

        result = compute_all_greeks(
            trades_df, curve_df, eval_date,
            vol_connector=vol_connector,
            bump_bp=bump_bp,
        )

        app.config["CPG_GREEKS"] = result
        return jsonify(result)

    except Exception as e:
        import traceback
        return jsonify({"error": f"{e}\n{traceback.format_exc()}"}), 500


@app.route("/cpg/api/price_extendible", methods=["POST"])
@require_curve
def cpg_price_extendible():
    """
    Price an extendible épargne à terme (prorogeable) using the deterministic term-dependent spread architecture.

    Supports both types:
    - COUPON: annual fixed coupons (like BNC NBC37043)
    - LINEAR ACCRUAL: cumulative linear interest (like BNC NBC37041)

    Body JSON:
    {
        "cpg_type": "COUPON" or "LINEAR ACCRUAL",
        "fundserv": "NBC37043",
        "notional": 10000,
        "client_rate": 4.10,
        "emission": "2025-10-02",
        "initial_maturity": "2026-10-02",
        "final_maturity": "2035-10-02",
        "freq_per_year": 1,
        "eval_date": "2026-02-26"
    }
    """
    try:
        curve_df = app.config["CPG_CURVE"]

        from cpg.extendible import (
            ExtendibleCPG, SpreadTermStructure,
            price_extendible_cpg, compute_cs01,
        )
        from cpg.pricing import has_curve_decomposition

        if not has_curve_decomposition(curve_df):
            return jsonify({"error": "La courbe doit avoir les colonnes ZeroCouponBase et ZeroCouponSpreadCDF pour le pricing prorogeable."})

        data = request.json or {}

        # Validation
        clean, err = validate_deal(data)
        if err:
            return jsonify({"error": f"Paramètres invalides: {err}"}), 400

        eval_date_str = clean["eval_date"] or "2026-02-26"
        eval_date = datetime.strptime(eval_date_str, "%Y-%m-%d")

        cpg = ExtendibleCPG.from_prospectus(
            cusip=clean.get("cusip", ""),
            fundserv=clean["fundserv"],
            notional=clean["notional"],
            client_rate=clean["client_rate"],
            cpg_type=clean["cpg_type"],
            emission=datetime.strptime(clean["emission"], "%Y-%m-%d"),
            initial_maturity=datetime.strptime(clean["initial_maturity"], "%Y-%m-%d"),
            final_maturity=datetime.strptime(clean["final_maturity"], "%Y-%m-%d"),
            freq_per_year=clean["freq_per_year"],
        )

        # Build spread structures
        spread_initial = SpreadTermStructure(curve_df)  # frozen at emission
        spread_market = SpreadTermStructure(curve_df)   # current market

        result = price_extendible_cpg(
            cpg, curve_df, spread_initial, spread_market, eval_date,
        )

        # Also compute CS01
        cs01 = compute_cs01(
            app.config.get("CPG_TRADES") if app.config.get("CPG_TRADES") is not None else None,
            curve_df, eval_date_str,
        ) if app.config.get("CPG_TRADES") is not None else {"CS01": 0, "note": "Pas de trades chargés"}

        result["cs01"] = cs01
        app.config["CPG_LAST_EXPORT"] = {"type": "extendible", "data": result}
        return jsonify(result)

    except Exception as e:
        import traceback
        return jsonify({"error": f"{e}\n{traceback.format_exc()}"}), 500


@app.route("/cpg/api/upload_ext_trades", methods=["POST"])
def cpg_upload_ext_trades():
    """Upload extendible épargne à terme trades from Excel/CSV and parse into deal definitions."""
    try:
        f = request.files.get("file")
        if not f:
            return jsonify({"error": "Aucun fichier"})
        import pandas as pd
        ext = os.path.splitext(f.filename)[1].lower()
        tmp = os.path.join(tempfile.gettempdir(), "cpg_ext" + ext)
        f.save(tmp)

        if ext in (".xlsx", ".xls"):
            raw = pd.read_excel(tmp, engine="openpyxl")
        else:
            raw = pd.read_csv(tmp)

        # Normalize column names
        col_map = {}
        for c in raw.columns:
            cl = str(c).strip().lower().replace(" ", "").replace("_", "").replace("é", "e").replace("è", "e")
            if "codetrans" in cl or cl == "type":
                col_map[c] = "CodeTransaction"
            elif "fundserv" in cl:
                col_map[c] = "FundServ"
            elif "montant" in cl or "notional" in cl or "amount" in cl:
                col_map[c] = "Montant"
            elif "coupon" in cl or "rate" in cl or "taux" in cl:
                col_map[c] = "Coupon"
            elif "marge" in cl or "margin" in cl or "spread" in cl:
                col_map[c] = "Marge"
            elif "emission" in cl or "issue" in cl:
                col_map[c] = "DateEmission"
            elif "echeanceinit" in cl or "initial" in cl or "firstmat" in cl:
                col_map[c] = "DateEcheanceInitial"
            elif "echeancefinal" in cl or "final" in cl or "maturity" in cl:
                col_map[c] = "DateEcheanceFinal"
            elif "freq" in cl:
                col_map[c] = "Frequence"
        raw = raw.rename(columns=col_map)

        required = {"CodeTransaction", "Montant", "Coupon", "DateEmission", "DateEcheanceFinal"}
        missing = required - set(raw.columns)
        if missing:
            return jsonify({"error": f"Colonnes manquantes: {missing}"})

        deals = []
        freq_map = {"annuel": 1, "annual": 1, "semestriel": 2, "semiannual": 2,
                     "trimestriel": 4, "quarterly": 4, "maturité": 0, "maturity": 0,
                     "maturite": 0, "0": 0, "1": 1, "2": 2, "4": 4, "12": 12}

        for _, row in raw.iterrows():
            code = str(row.get("CodeTransaction", "COUPON")).upper().strip()
            freq_raw = str(row.get("Frequence", "1" if code == "COUPON" else "0")).strip().lower()
            freq = freq_map.get(freq_raw, int(freq_raw) if freq_raw.isdigit() else 1)
            emission = str(row["DateEmission"]).strip().split()[0]
            init_mat = str(row.get("DateEcheanceInitial", row["DateEcheanceFinal"])).strip().split()[0]
            final_mat = str(row["DateEcheanceFinal"]).strip().split()[0]
            montant = float(str(row["Montant"]).replace("$", "").replace(" ", "").replace(",", ""))
            coupon = float(str(row["Coupon"]).replace("%", "").strip())
            marge = float(str(row.get("Marge", 0)).replace("%", "").strip()) if pd.notna(row.get("Marge")) else 0

            deals.append({
                "cpg_type": code,
                "fundserv": str(row.get("FundServ", "")),
                "notional": montant,
                "client_rate": coupon + marge,
                "emission": emission,
                "initial_maturity": init_mat,
                "final_maturity": final_mat,
                "freq_per_year": freq,
            })

        app.config["CPG_EXT_DEALS"] = deals
        return jsonify({"count": len(deals), "deals": deals})

    except Exception as e:
        import traceback
        return jsonify({"error": f"{e}\n{traceback.format_exc()}"}), 500


@app.route("/cpg/api/download_ext_template")
def cpg_download_ext_template():
    """Download an Excel template for extendible épargne à terme."""
    import pandas as pd
    cols = ["CodeTransaction", "FundServ", "Montant", "Coupon", "Marge",
            "DateEmission", "DateEcheanceInitial", "DateEcheanceFinal", "Frequence"]
    sample = [
        ["COUPON", "NBC37043", 10000, 4.10, 0, "2025-10-02", "2026-10-02", "2035-10-02", "Annuel"],
        ["LINEAR ACCRUAL", "NBC37041", 10000, 6.05, 0, "2025-10-02", "2026-10-02", "2040-10-02", "Maturité"],
    ]
    df = pd.DataFrame(sample, columns=cols)
    tmp = os.path.join(tempfile.gettempdir(), "ext_template.xlsx")
    df.to_excel(tmp, index=False, engine="openpyxl")
    return send_file(tmp, as_attachment=True, download_name="cpg_prorogeable_template.xlsx")


@app.route("/cpg/api/full_results", methods=["POST"])
@require_curve
def cpg_full_results():
    """Comprehensive Bloomberg-grade results — produces all 10 blocks for the JS renderFullResults()."""
    try:
        curve_df = app.config["CPG_CURVE"]

        data = request.json or {}

        # Validation
        clean, err = validate_deal(data)
        if err:
            return jsonify({"error": f"Paramètres invalides: {err}"}), 400

        # Cache: skip recomputation if inputs unchanged
        import hashlib
        cache_key = hashlib.md5(json.dumps(data, sort_keys=True).encode()).hexdigest()
        cached = app.config.get("_FULL_RESULTS_CACHE")
        if cached and cached.get("key") == cache_key:
            return jsonify(cached["result"])

        eval_date = clean["eval_date"] or "2026-02-26"

        from cpg.extendible import (
            ExtendibleCPG, SpreadTermStructure, price_extendible_cpg,
        )
        from cpg.pricing import has_curve_decomposition, build_discount_function

        if not has_curve_decomposition(curve_df):
            return jsonify({"error": "Décomposition OIS/Spread requise"})

        cpg_type = clean["cpg_type"]
        notional = clean["notional"]
        client_rate = clean["client_rate"]
        freq = clean["freq_per_year"]

        cpg = ExtendibleCPG.from_prospectus(
            cusip=clean.get("cusip", ""),
            fundserv=clean["fundserv"],
            notional=notional, client_rate=client_rate,
            cpg_type=cpg_type,
            emission=datetime.strptime(clean["emission"], "%Y-%m-%d"),
            initial_maturity=datetime.strptime(clean["initial_maturity"], "%Y-%m-%d"),
            final_maturity=datetime.strptime(clean["final_maturity"], "%Y-%m-%d"),
            freq_per_year=freq,
        )
        spread_i = SpreadTermStructure(curve_df)
        spread_m = SpreadTermStructure(curve_df)
        eval_dt = datetime.strptime(eval_date, "%Y-%m-%d")
        pr = price_extendible_cpg(cpg, curve_df, spread_i, spread_m, eval_dt)

        pv = pr["PV_total"]; pvf = pr["PV_fixed"]; opt = pr["option_value"]

        # --- Greeks: auto-create synthetic trade if no portfolio loaded ---
        g_out = {"available": False, "note": "Greeks en cours..."}
        scen_out = []
        kr_out = {"available": False, "note": "KRR en cours..."}
        trades_df = app.config.get("CPG_TRADES")
        if trades_df is None:
            # Create a synthetic single-trade DataFrame from deal params
            import pandas as pd
            from datetime import datetime as _dt
            trades_df = pd.DataFrame([{
                "CodeTransaction": data.get("cpg_type", "COUPON"),
                "Inventaire": "PRORO",
                "Contrepartie": "",
                "DateEmission": _dt.strptime(data["emission"], "%Y-%m-%d"),
                "DateEcheanceInitial": _dt.strptime(data["initial_maturity"], "%Y-%m-%d"),
                "DateEcheanceFinal": _dt.strptime(data["final_maturity"], "%Y-%m-%d"),
                "Montant": float(data.get("notional", 10000)),
                "Coupon": float(data.get("client_rate", 4.10)),
                "Marge": 0.0,
                "Frequence": str(data.get("freq_per_year", 1)),
                "FreqPerYear": int(data.get("freq_per_year", 1)),
                "BaseCalcul": "ACT/365",
                "Devise": "CAD",
                "CUSIP": data.get("fundserv", ""),
                "FundServ": data.get("fundserv", ""),
            }])
        if trades_df is not None and len(trades_df) > 0:
            from cpg.greeks import compute_all_greeks
            ga = compute_all_greeks(trades_df, curve_df, eval_date,
                                     vol_connector=app.config.get("CPG_VOL_CONNECTOR"), bump_bp=1.0)
            d01 = ga.get("dv01", {}); gam = ga.get("gamma", {}); th = ga.get("theta", {})
            ve = ga.get("vega", {}); cs = ga.get("cs01", {})
            g_out = {
                "available": True, "dv01": d01.get("DV01", 0), "dv01_method": d01.get("method", ""),
                "cs01": cs.get("CS01", 0), "gamma_1bp": gam.get("Gamma_1bp", 0),
                "theta_1d": th.get("Theta_1d", 0), "theta_1m": th.get("Theta_1m", 0),
                "carry_bps": th.get("carry_bps", 0), "vega_1bp": ve.get("Vega_1bp", 0),
                "vega_source": ve.get("source", ""), "pv_base": ga.get("PV_base", 0),
                "curve_method": ga.get("curve_model", {}).get("method", ""),
            }
            scen_out = ga.get("scenarios", [])
            kr_raw = ga.get("key_rate_dv01", {})
            tot_kr = sum(abs(v) for v in kr_raw.values())
            kr_out = {
                "available": True, "dv01_global": d01.get("DV01", 0), "shift": "1",
                "bump_method": d01.get("method", ""), "curve_bumped": "OIS CORRA",
                "buckets": [{"bucket": k, "dv01": round(v, 4),
                             "pct_total": round(abs(v) / tot_kr * 100, 1) if tot_kr > 0 else 0}
                            for k, v in kr_raw.items()],
            }

        # --- Cashflows (built inline) ---
        cf_rows = []
        rate_dec = client_rate / 100.0
        if cpg_type == "COUPON" and freq > 0:
            try:
                em = datetime.strptime(data["emission"], "%Y-%m-%d")
                fm = datetime.strptime(data["final_maturity"], "%Y-%m-%d")
                months_per = 12 // freq
                cur = em; n = 0
                while True:
                    m = cur.month + months_per
                    y = cur.year + (m - 1) // 12
                    m = (m - 1) % 12 + 1
                    d_day = min(cur.day, 28)
                    cur = datetime(y, m, d_day)
                    if cur > fm: break
                    n += 1
                    cf_rows.append({"pay_date": cur.strftime("%Y-%m-%d"), "type": "Coupon",
                                    "receive": round(notional * rate_dec / freq, 2), "pay": 0,
                                    "net": round(notional * rate_dec / freq, 2), "df": 0, "pv": 0})
                cf_rows.append({"pay_date": data["final_maturity"], "type": "Principal",
                                "receive": round(notional, 2), "pay": 0, "net": round(notional, 2), "df": 0, "pv": 0})
            except Exception:
                pass
        elif cpg_type == "LINEAR ACCRUAL":
            sched = pr.get("remboursement_schedule", [])
            if sched:
                last = sched[-1]
                total = last.get("remboursement_amount", notional)
                cf_rows.append({"pay_date": data["final_maturity"], "type": "P+I cumulé",
                                "receive": round(total, 2), "pay": 0, "net": round(total, 2), "df": 0, "pv": 0})
        total_receive = sum(r["receive"] for r in cf_rows)
        total_net = sum(r["net"] for r in cf_rows)

        # --- Curve summary ---
        cu_rows = []
        for _, r in curve_df.iterrows():
            row = {"term": f"{int(r['termPoint'])} {r['termType']}", "days": int(r["ApproxDays"]),
                   "years": round(r["ApproxDays"] / 365.0, 2),
                   "market_rate": round(float(r["TauxCDF"]), 4)}
            if has_curve_decomposition(curve_df):
                import pandas as pd
                row["zero_rate_ois"] = round(float(r["ZeroCouponBase"]), 4) if pd.notna(r.get("ZeroCouponBase")) else 0
                row["zero_rate_spread"] = round(float(r["ZeroCouponSpreadCDF"]), 4) if pd.notna(r.get("ZeroCouponSpreadCDF")) else 0
            import math as _m
            row["discount_factor"] = round(_m.exp(-float(r["TauxCDF"]) / 100.0 * r["ApproxDays"] / 365.0), 6)
            cu_rows.append(row)

        # --- Schedule ---
        exs = pr.get("exercise_analysis", [])
        sc_rows = [dict(e, n=i + 1) for i, e in enumerate(exs)]

        # --- Vol monitor ---
        vm = {"available": False, "source": "Aucune surface chargée", "note": "Générer un proxy ou charger un fichier (Étape 1)."}
        vc = app.config.get("CPG_VOL_CONNECTOR")
        if vc and hasattr(vc, "get_vol_matrix"):
            try:
                mat = vc.get_vol_matrix()
                if mat:
                    vm = {"available": True, "source": getattr(vc, "vol_source", ""),
                          "n_expiries": len(mat["expiry_grid"]), "n_tenors": len(mat["tenor_grid"]),
                          "expiry_grid": mat["expiry_grid"], "tenor_grid": mat["tenor_grid"],
                          "matrix": mat["vol_matrix"]}
            except Exception:
                pass

        result = {
            "deal_summary": {
                "style": "Bermudan (Extendible)" if len(exs) > 1 else "European",
                "position": "Long (issuer holds extension right)",
                "type": cpg_type,
                "product": "Épargne à terme prorogeable",
                "currency": "CAD", "notional": notional,
                "strike": client_rate,
                "first_exercise": data.get("initial_maturity", "—"),
                "swap_start": data.get("emission", "—"),
                "swap_end": data.get("final_maturity", "—"),
                "settlement": "Physical (capital + intérêts)",
                "n_exercise_dates": len(exs),
                "min_years": pr.get("min_years", 0), "max_years": pr.get("max_years", 0),
                "model": "HW1F trinomial + DCF", "vol_type": "Normal (bp)",
                "curve_date": eval_date, "fundserv": data.get("fundserv", ""),
            },
            "valuation": {
                "npv": round(pv, 2), "npv_without_fee": round(pv, 2),
                "pv_fixed": round(pvf, 2), "option_value": round(opt, 2),
                "option_method": pr.get("option_method", "intrinsic"),
                "atm_strike": client_rate,
                "premium_pct": round(opt / notional * 100, 4) if notional else 0,
                "pv_notional_ratio": round(pv / notional * 100, 2) if notional else 0,
                "intrinsic_value": round(pr.get("intrinsic_value", opt), 2),
                "time_value": round(pr.get("time_value", 0), 2),
                "time_value_note": pr.get("option_method", ""),
                "hw_vega_1bp": round(pr.get("hw_vega_1bp", 0), 4),
                "hw_vega_source": pr.get("hw_vega_source", ""),
                "hw_sigma_bp": pr.get("hw_sigma_bp", 65),
                "hw_mean_reversion": pr.get("hw_mean_reversion", 0.03),
                "yield_value_bp": round(opt / notional * 10000, 2) if notional else 0,
            },
            "premium": {
                "option_premium": round(opt, 2),
                "option_premium_pct": round(opt / notional * 100, 4) if notional else 0,
                "underlying_premium": round(pvf, 2),
                "underlying_premium_pct": round(pvf / notional * 100, 4) if notional else 0,
                "total_premium": round(pv, 2),
                "option_over_total_pct": round(opt / pv * 100, 2) if pv else 0,
            },
            "greeks": g_out,
            "schedule": {
                "n_exercises": len(sc_rows),
                "itm_count": sum(1 for e in sc_rows if e.get("in_the_money")),
                "rows": sc_rows,
            },
            "cashflows": {
                "n_cashflows": len(cf_rows), "total_receive": round(total_receive, 2),
                "total_pay": 0, "total_net": round(total_net, 2),
                "pv_total": round(pvf, 2), "rows": cf_rows,
            },
            "curve_summary": {
                "curve_name": "CAD CDF (OIS CORRA + Spread)" if has_curve_decomposition(curve_df) else "CAD CDF",
                "components": "CAD OIS CORRA + CAD CDF Spread" if has_curve_decomposition(curve_df) else "CAD CDF",
                "interpolation": "Linéaire taux ZC",
                "curve_date": eval_date,
                "dv01_calc_type": "OIS-only bump (spread fixe)",
                "n_points": len(cu_rows),
                "range_days": str(cu_rows[0]["days"])+"j - "+str(cu_rows[-1]["days"])+"j" if cu_rows else "-",
                "decomposition_available": bool(has_curve_decomposition(curve_df)),
                "short_rate": cu_rows[0]["market_rate"] if cu_rows else 0,
                "long_rate": cu_rows[-1]["market_rate"] if cu_rows else 0,
                "points": cu_rows,
            },
            "calibration": {
                "model": "Hull-White 1-Factor", "calibration_method": "Trinomial (calibré sur structure à terme)",
                "mean_reversion": "N/A", "sigma_type": "Normal vol (bp)",
                "pricing_engine": "HW1F trinomial (backward induction) + DCF",
                "spread_treatment": "Déterministe terme-dépendant",
                "status": "Mode intrinsic — calibration HW disponible pour Bermudan swaptions",
                "note": "Strike = taux client − spread initial (figé). Sous-jacent = par rate de financement. Backward induction HW1F pour la time value.",
            },
            "vol_monitor": vm,
            "key_rate_risk": kr_out,
            "scenarios": scen_out,
            "exercise_analysis": exs,
            "remboursement_schedule": pr.get("remboursement_schedule", []),
            "spread_initial": pr.get("spread_initial"),
            "pricing_raw": pr,
        }
        # Clean numpy types for JSON
        import numpy as _np
        def _c(o):
            if isinstance(o, dict): return {k: _c(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)): return [_c(v) for v in o]
            if isinstance(o, (_np.integer,)): return int(o)
            if isinstance(o, (_np.floating,)): return float(o)
            if isinstance(o, (_np.bool_,)): return bool(o)
            if isinstance(o, _np.ndarray): return _c(o.tolist())
            return o
        app.config["CPG_LAST_EXPORT"] = {"type": "full", "data": result}
        clean = _c(result)
        app.config["_FULL_RESULTS_CACHE"] = {"key": cache_key, "result": clean}
        return jsonify(clean)

    except Exception as e:
        import traceback
        return jsonify({"error": f"{e}\n{traceback.format_exc()}"}), 500


@app.route("/cpg/api/vol/upload", methods=["POST"])
def cpg_vol_upload():
    """Upload a vol surface (CSV/Excel) — explicit vol mode."""
    try:
        f = request.files.get("file")
        if not f:
            return jsonify({"error": "Aucun fichier"})

        ext = os.path.splitext(f.filename)[1].lower()
        tmp = os.path.join(tempfile.gettempdir(), "cpg_vol" + ext)
        f.save(tmp)

        from cpg.bloomberg import BloombergConnector
        bbg = BloombergConnector(mode="file")
        df = bbg.load_vol_surface(tmp)
        app.config["CPG_VOL_CONNECTOR"] = bbg

        matrix = bbg.get_vol_matrix()
        return jsonify({
            "points": len(df),
            "source": bbg.vol_source,
            "as_of": bbg.vol_as_of,
            "expiry_grid": matrix["expiry_grid"],
            "tenor_grid": matrix["tenor_grid"],
            "vol_matrix": matrix["vol_matrix"],
        })

    except Exception as e:
        import traceback
        return jsonify({"error": f"{e}\n{traceback.format_exc()}"}), 500


@app.route("/cpg/api/vol/proxy", methods=["POST"])
def cpg_vol_proxy():
    """Generate a proxy vol surface from parameters."""
    try:
        data = request.json or {}
        from cpg.bloomberg import BloombergConnector
        bbg = BloombergConnector(mode="file")
        df = bbg.generate_proxy_surface(
            vol_base_bp=float(data.get("vol_base", 65)),
            slope_per_year=float(data.get("slope", -2)),
            floor_bp=float(data.get("floor", 30)),
            smile_curvature=float(data.get("smile", 0)),
        )
        app.config["CPG_VOL_CONNECTOR"] = bbg

        matrix = bbg.get_vol_matrix()
        return jsonify({
            "points": len(df),
            "source": bbg.vol_source,
            "as_of": bbg.vol_as_of,
            "expiry_grid": matrix["expiry_grid"],
            "tenor_grid": matrix["tenor_grid"],
            "vol_matrix": matrix["vol_matrix"],
        })

    except Exception as e:
        import traceback
        return jsonify({"error": f"{e}\n{traceback.format_exc()}"}), 500


@app.route("/cpg/api/vol/status")
def cpg_vol_status():
    """Return current vol surface status."""
    bbg = app.config.get("CPG_VOL_CONNECTOR")
    if bbg is None or not bbg.has_vol:
        return jsonify({"loaded": False, "source": "none"})

    matrix = bbg.get_vol_matrix()
    return jsonify({
        "loaded": True,
        "source": bbg.vol_source,
        "as_of": bbg.vol_as_of,
        "expiry_count": len(matrix["expiry_grid"]),
        "tenor_count": len(matrix["tenor_grid"]),
    })


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN — startup
# ═══════════════════════════════════════════════════════════════════════════

def open_browser():
    webbrowser.open("http://localhost:5050/cpg")


if __name__ == "__main__":
    print("=" * 64)
    print("  Desjardins — Portail Trésorerie")
    print("  http://localhost:5050/cpg")
    print("=" * 64)
    print("  Ctrl+C pour arrêter\n")
    threading.Timer(1.5, open_browser).start()
    app.run(host="127.0.0.1", port=5050, debug=False)

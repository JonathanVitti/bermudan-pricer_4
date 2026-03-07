# Bermudan Swaption Pricer

CAD CORRA OIS — Hull-White 1F — Calibration hybride (v12)

## Installation

```bash
git clone https://github.com/TON_USER/bermudan-pricer.git
cd bermudan-pricer

python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Mac/Linux

pip install -r requirements.txt
```

## Structure

```
bermudan-pricer/
├── README.md
├── requirements.txt
├── .gitignore
├── config/
│   ├── config.yaml              ← Config YAML (éditer ici)
│   └── deal_template.xlsx       ← Template Excel pré-rempli
├── src/
│   ├── pricer.py                ← Moteur de pricing
│   ├── bbg_fetcher.py           ← Fetch Bloomberg / lecture manuelle
│   └── excel_bridge.py          ← Pont Excel ↔ Pricer
└── output/                      ← Résultats (ignoré par git)
```

## Utilisation

### Via YAML
```bash
python src/pricer.py
python src/pricer.py --config config/config.yaml
```

### Via Excel
```bash
python src/excel_bridge.py config/deal_template.xlsx
```

### Via Python
```python
import yaml, sys, os
sys.path.insert(0, "src")
from pricer import BermudanPricer
from bbg_fetcher import fetch_all

with open("config/config.yaml") as f:
    cfg = yaml.safe_load(f)

mkt = fetch_all(cfg, config_dir="config")
p = BermudanPricer(cfg, mkt)
p.setup()
p.calibrate()
p.compute_greeks()
print(f"NPV = {p.npv:,.2f}")
```

## Données requises (3 inputs)

| # | Donnée | Source BBG | Format |
|---|--------|------------|--------|
| 1 | Courbe de discount | ICVS → Curve Horizon | dates + DF |
| 2 | Surface ATM | VCUB ou BVOL | matrice BPx10 |
| 3 | NPV Bloomberg | SWPM → Valuation | un nombre |

## Méthodologie

**σ_total = σ_ATM + Δσ_spread**

1. σ_ATM : calibré sur européennes co-terminales (vols ATM)
2. σ_inverse : Brent solver → match NPV BBG exactement
3. Δσ_spread = σ_inverse − σ_ATM (capture le skew)

Vega hybride : bump ATM ±1bp → recalibre σ_ATM → ajoute Δσ fixe → reprice

## Bloomberg API

Si `data_mode: bloomberg` dans le config, fetch automatique de la courbe et vol surface.
NPV toujours à entrer manuellement. Si blpapi absent → fallback mode manuel.

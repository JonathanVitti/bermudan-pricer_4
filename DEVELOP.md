# Guide développeur — Comment modifier le pricer

Ce guide explique comment modifier et étendre le pricer CPG **après le départ du développeur initial**. L'architecture est conçue pour que chaque type de modification soit localisé dans un seul fichier.

---

## Où modifier quoi

| Je veux... | Fichier à modifier |
|-----------|-------------------|
| Changer le taux/coupon/dates d'un deal | Interface web ou fichier Excel |
| Ajouter un nouveau type de CPG | `src/cpg/pricing.py` + `src/cpg/extendible.py` |
| Modifier la logique d'actualisation | `src/cpg/pricing.py` → `build_discount_function()` |
| Modifier les Greeks (DV01, CS01, etc.) | `src/cpg/greeks.py` |
| Ajouter un scénario de stress | `src/cpg/greeks.py` → `compute_scenarios()` |
| Changer les buckets KR-DV01 | `src/cpg/greeks.py` → `KR_BUCKETS` dict |
| Ajouter une source de données (Bloomberg, API) | `src/cpg/providers.py` → nouvelle classe |
| Modifier la courbe SQL | `src/cpg/curve_sql.py` ou la route dans `app.py` |
| Modifier l'interface web | `app.py` → `CPG_BODY` (HTML/CSS/JS inline) |
| Ajouter une page dans l'interface | `app.py` → sidebar nav + page div + JS functions |
| Modifier l'export Excel | `src/cpg/export.py` |
| Ajouter un test | `tests/test_cpg.py` ou `tests/test_quant_invariants.py` |

---

## Ajouter un nouveau type de CPG

Exemple : ajouter un type "STEP-UP" (coupon croissant).

### 1. Dans `src/cpg/pricing.py`

Ajouter la fonction de pricing :

```python
def price_step_up(notional, coupon_schedule, emission, maturity, eval_date, df_func):
    """Price a STEP-UP CPG with increasing coupons."""
    # coupon_schedule = [(date, rate), ...] 
    pv = 0.0
    for dt, rate in coupon_schedule:
        if dt <= eval_date:
            continue
        days = _days_between(eval_date, dt)
        cf = notional * rate / 100.0
        pv += cf * df_func(days)
    # + principal
    pv += notional * df_func(_days_between(eval_date, maturity))
    return {"PV": pv, ...}
```

### 2. Dans `price_single_cpg()`, ajouter le dispatch :

```python
elif code == "STEP-UP":
    result = price_step_up(...)
```

### 3. Dans `src/cpg/trades.py`, ajouter le type supporté :

```python
SUPPORTED_TYPES = {"COUPON", "LINEAR ACCRUAL", "STEP-UP"}
```

### 4. Ajouter un test dans `tests/test_cpg.py`

### 5. Mettre à jour le golden file si le portefeuille test change

---

## Ajouter une source Bloomberg

Le fichier `src/cpg/providers.py` contient un `BloombergProvider` prêt à être branché.

### 1. Installer blpapi

```
pip install blpapi
```

### 2. Implémenter `fetch_curve()` dans `BloombergProvider`

Le pseudo-code est déjà dans le fichier. Remplacer le `raise NotImplementedError` par l'appel blpapi réel.

### 3. Configurer dans `config/config.yaml`

```yaml
market_data:
  source: bloomberg
  bloomberg:
    curve_ticker: S490
    vol_ticker: VCUB
```

Le pricer utilisera automatiquement Bloomberg au lieu de SQL ou fichier.

---

## Lancer les tests

```bash
python -m pytest tests/ -v
```

Les tests couvrent :
- **test_cpg.py** : parsing des trades, pricing de base, pipeline complet
- **test_quant_invariants.py** : régression golden (PV stable), stabilité DV01, CS01=DV01 pour cashflows fixes, intégrité des bumps

Si un test de régression échoue après une modification intentionnelle du pricing, mettre à jour le golden file :

```bash
python -c "
from cpg.trades import load_trades_file
from cpg.curve_sql import load_curve_from_csv
from cpg.pricing import price_cpg_portfolio
import json

trades = load_trades_file('data/trades_sample.csv')
curve = load_curve_from_csv('data/curve_sample.csv')
res = price_cpg_portfolio(trades, curve, '2026-02-26')
ok = res[res['Status']=='OK'].sort_values(['CodeTransaction','DateEcheanceFinal'])
golden = {'eval_date':'2026-02-26', 'n':len(ok), 'pv_total':float(ok['PV'].sum()),
          'pv_by_trade':[round(float(x),6) for x in ok['PV'].tolist()]}
with open('tests/golden/cpg_portfolio_2026-02-26.json','w') as f:
    json.dump(golden, f, indent=2)
print('Golden mis à jour.')
"
```

---

## Architecture courbe duale (OIS + Spread)

Le pricer sépare la courbe CDF en deux composantes :

- **OIS CORRA** (taux de marché) → bumps pour les Greeks de hedging
- **Spread CDF** (coût de funding Desjardins) → fixe dans les bumps

Quand on calcule le DV01, on bumpe uniquement l'OIS. Le spread ne bouge pas. Le DV01 obtenu est directement le notionnel d'IRS à mettre en face pour hedger.

Le CS01 fait l'inverse : bumpe le spread, garde l'OIS fixe. Pour les cashflows fixes, CS01 ≈ DV01. Pour les prorogeables, CS01 ≠ DV01.

---

## Architecture prorogeables (Marc-Antoine)

Pour les CPG prorogeables, le pricing utilise l'architecture strike vs sous-jacent :

- **Strike** = taux client − spread initial (terme-dépendant, fixé à l'émission)
- **Sous-jacent** = OIS forward (stochastique via HW) + spread marché actuel (input exogène)

Si le crédit de la banque se détériore, le sous-jacent monte → la prorogation devient plus avantageuse → l'option vaut plus. Le strike ne bouge pas.

Actuellement : valeur intrinsèque seulement. La time value (HW bermudien) est en développement.

---

## Conventions

- **Taux** : en pourcentage (ex: 2.50 = 2.50%, pas 0.025)
- **Bumps** : en basis points (1bp = 0.01%)
- **Jours** : ACT/365 par défaut
- **Devise** : CAD uniquement
- **Date format** : YYYY-MM-DD partout

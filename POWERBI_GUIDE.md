# Power BI Dashboard — Setup Guide

## Étape 1 : Générer les données

```powershell
cd "C:\Users\DDB3277\OneDrive - Desjardins\Documents\bermudan-pricer"
python run_and_export.py --config config\config.yaml --output output\pbi_data.xlsx
```

Ça crée `output\pbi_data.xlsx` avec 6 tables structurées.

## Étape 2 : Créer le rapport Power BI

1. Ouvre **Power BI Desktop**
2. **Get Data** → **Excel Workbook**
3. Navigue vers `output\pbi_data.xlsx`
4. Coche les 6 tables :
   - `tblSummary` — Métriques principales (1 ligne)
   - `tblComparison` — BBG vs QuantLib (valuation)
   - `tblGreeks` — Greeks avec BBG comparison
   - `tblCurve` — Courbe de discount
   - `tblVol` — Surface de vol ATM
   - `tblRunLog` — Métadonnées du run
5. Cliquez **Load**

## Étape 3 : Créer les visuels

### Page 1 : Dashboard principal

**KPI Cards** (en haut) :
- Drag `tblSummary.NPV` → Card visual
- Drag `tblSummary.sigma_total_bp` → Card
- Drag `tblSummary.YieldValue_bp` → Card
- Drag `tblSummary.ATM_pct` → Card

**Graphique à barres — BBG Comparison** :
- Visual: Clustered Bar Chart
- Axis: `tblComparison.Metric`
- Values: `tblComparison.Bloomberg`, `tblComparison.QuantLib`
- Filtre: exclure "NPV" (échelle trop différente)

**Graphique à barres — Greeks** :
- Visual: Clustered Bar Chart
- Axis: `tblGreeks.Greek`
- Values: `tblGreeks.Value`, `tblGreeks.BBG`

**Table détaillée** :
- Visual: Table
- Columns: toutes les colonnes de `tblComparison`
- Conditional formatting: `Diff_pct` → vert si |x| < 3%, jaune si < 10%, rouge sinon

### Page 2 : Market Data

**Courbe de discount** :
- Visual: Line Chart
- Axis: `tblCurve.Date`
- Values: `tblCurve.DiscountFactor`

**Heatmap Vol Surface** :
- Visual: Matrix
- Rows: `tblVol.Expiry`
- Columns: (unpivot tenor columns)
- Values: vol values
- Conditional formatting: Background color scale

### Page 3 : Run History (optionnel)
Si tu exécutes le pricer plusieurs fois avec des dates différentes,
tu peux configurer Power BI pour append les résultats au lieu de remplacer.

## Étape 4 : Refresh automatique

Quand tu re-runs le pricer :
```powershell
python run_and_export.py --config config\config.yaml
```

Dans Power BI : **Home → Refresh** pour recharger les nouvelles données.

## Mesures DAX utiles

```dax
// Couleur conditionnelle pour Diff_pct
DiffColor = 
    SWITCH(TRUE(),
        ABS([Diff_pct]) < 3, "#22C55E",    // Vert
        ABS([Diff_pct]) < 10, "#F59E0B",   // Jaune
        "#EF4444"                            // Rouge
    )

// NPV Match
NPV_Match = 
    VAR bbg = FIRSTNONBLANK(tblSummary[NPV], 1)
    VAR ql = bbg  // NPV is always matched by construction
    RETURN FORMAT(0, "0.0000%")
```

## Tips

- Pour le refresh auto : **File → Options → Data Load → cocher "Auto"**
- Tu peux publier sur Power BI Service si disponible
- Le fichier Excel est overwritten à chaque run → le rapport se met à jour automatiquement

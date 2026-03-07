# Déploiement — Portail Trésorerie Desjardins

## Installation (un clic)

1. Extraire le fichier `bermudan-pricer.zip` dans un dossier (ex: `C:\Desjardins\Pricer\`)
2. Double-cliquer sur **`INSTALLER.bat`**

C'est tout. Le script :
- Détecte Python automatiquement (venv, PATH, py launcher, dossiers courants)
- Si Python n'est pas installé, propose de le télécharger et l'installer (sans droits admin)
- Crée l'environnement virtuel (`venv/`)
- Installe les dépendances (`flask`, `numpy`, `scipy`, `pandas`, `openpyxl`)
- Crée un raccourci **« Portail Trésorerie Desjardins »** sur le Bureau avec l'alvéole Desjardins
- Lance l'application

## Utilisation quotidienne

Double-cliquer sur le raccourci Bureau **« Portail Trésorerie Desjardins »** (alvéole verte).

Ou : double-cliquer sur **`Lancer.bat`** dans le dossier.

L'application s'ouvre automatiquement dans le navigateur à `http://localhost:5050/cpg`.

## Dépannage

| Problème | Solution |
|----------|----------|
| « Python introuvable » | Relancer `INSTALLER.bat` — il propose l'installation automatique |
| « Dépendances manquantes » | Supprimer le dossier `venv/` et relancer `INSTALLER.bat` |
| Port 5050 occupé | Modifier `port=5050` dans `app.py` (dernière ligne) |
| L'app ne s'ouvre pas | Aller manuellement à `http://localhost:5050/cpg` |

## Mode hors-ligne (réseau restreint)

Si la machine n'a pas accès à Internet :

1. Sur une machine avec Internet, créer le wheelhouse :
   ```
   pip download -r requirements.txt -d wheelhouse/
   ```
2. Copier le dossier `wheelhouse/` dans le dossier du pricer
3. `INSTALLER.bat` détectera automatiquement le mode hors-ligne

## Structure

```
bermudan-pricer/
├── INSTALLER.bat          ← Installation (un clic)
├── Lancer.bat             ← Lancement quotidien
├── app.py                 ← Application
├── requirements.txt       ← Dépendances
├── static/
│   ├── alveole-32.png     ← Favicon navigateur
│   ├── alveole.ico        ← Icône raccourci Windows
│   └── d15-desjardins-logo-couleur.png
├── fonts/                 ← Desjardins Sans
├── data/                  ← Exemples
├── src/                   ← Moteur de pricing
└── venv/                  ← (créé automatiquement)
```

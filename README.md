# Recherche d'appartements — Toulouse & Paris

Collecte automatisée d'annonces de location via Playwright + interception d'API.
Multi-ville : un preset par ville dans `configs/`, sorties dans `results/<ville>/`.

## Structure

```
configs/
  toulouse.json      Preset Toulouse (≤600€, centre, hors coloc)
  paris.json         Preset Paris (arrondissements 5/6/7/11/13/14/15/20, appart ou coloc)
results/
  toulouse/
    annonces.json    Dump brut (JSON)
    annonces.csv     Même chose en CSV
    rapport.md       Rapport markdown auto-généré (synthèse, tops, listes triées)
  paris/
    ...
scraper.py           Scraper Playwright (Bien'ici + tentatives LBC/PAP/SeLoger)
generate_report.py   Générateur de rapport markdown
set_criteria.py      Configuration interactive ou par CLI (presets)
search_config.json   Config courante (générée par set_criteria.py)
```

## Installation

```bash
python -m venv venv
source venv/bin/activate
pip install playwright beautifulsoup4 pandas
playwright install chromium
```

## Utilisation

**Lancer une recherche sur une ville avec son preset :**

```bash
python set_criteria.py --preset toulouse --run
python set_criteria.py --preset paris --run
```

En fin de run, le scraper écrit `annonces.json` + `annonces.csv` + `rapport.md`
dans `results/<ville>/`.

**Regénérer un rapport depuis un JSON existant :**

```bash
python generate_report.py --city Toulouse
python generate_report.py --city Paris
```

**Personnaliser un critère :**

```bash
python set_criteria.py --preset paris --max-price 800 --run
python set_criteria.py --city Toulouse --postal-codes 31000,31400 --max-price 650 --run
```

**Configuration interactive :**

```bash
python set_criteria.py
```

## Ajouter une nouvelle ville

1. Créer `configs/<ville>.json` (copier un preset existant, adapter codes postaux + quartiers).
2. Si besoin, enrichir le filtrage géographique dans `scraper.py` (`is_valid_<ville>()`).
3. Lancer `python set_criteria.py --preset <ville> --run`.

## Sources

- **Bien'ici** — source principale, interception API JSON (`realEstateAds`).
- **Leboncoin / SeLoger / Logic-Immo** — bloqués par Datadome (403), non exploitables
  sans proxy résidentiel payant.
- **PAP** — parsing HTML sur certaines villes ; résultats variables.

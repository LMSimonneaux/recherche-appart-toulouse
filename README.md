# Recherche appartement Toulouse

Scraper Playwright pour collecter des annonces de location à Toulouse centre-ville (≤600€ CC, hors colocation).

## Contenu

- `scraper.py` — script principal (Bien'ici via interception API, tentatives Leboncoin/PAP/SeLoger/Logic-Immo/LocService)
- `resultats_annonces.json` / `resultats_annonces.csv` — 49 annonces validées
- `rapport_recherche.md` — rapport détaillé (stats, top annonces, méthodologie)

## Utilisation

```bash
python -m venv venv && source venv/bin/activate
pip install playwright beautifulsoup4
playwright install chromium
python scraper.py

Configuration des critères:

- Utilisez [set_criteria.py](set_criteria.py) pour créer/éditer `search_config.json` (interactif):

```
python set_criteria.py
```

- Exemple en ligne de commande puis exécution:

```
python set_criteria.py --max-price 800 --postal-codes 31000,31400 --run
```
```

## Résultats

Source fiable : Bien'ici (API JSON interceptable). Leboncoin/SeLoger/Logic-Immo bloqués par Datadome.

#!/usr/bin/env python3
"""Génère `resultats_annonces.md` à partir de `resultats_annonces.json`."""
import json
import os
from collections import defaultdict
from datetime import datetime
from statistics import mean, median

IN = 'resultats_annonces.json'
CFG = 'search_config.json'
OUT = 'resultats_annonces.md'


def load_config():
    if os.path.exists(CFG):
        try:
            with open(CFG, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def fmt(v):
    return '' if v is None else str(v)


def main():
    try:
        with open(IN, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f'Fichier introuvable: {IN}')
        return

    cfg = load_config()
    entries = [e for e in data if isinstance(e, dict)]
    entries_sorted = sorted(entries, key=lambda e: (e.get('loyer_cc') is None, e.get('loyer_cc') or 0))

    prices = [e.get('loyer_cc') for e in entries if isinstance(e.get('loyer_cc'), (int, float))]
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    city = cfg.get('city', 'Inconnue')
    notes = cfg.get('notes', '')
    max_price = cfg.get('max_price', '?')
    postals = cfg.get('postal_codes', [])
    allow_coloc = cfg.get('allow_coloc', False)
    only_coloc = cfg.get('only_coloc', False)

    with open(OUT, 'w', encoding='utf-8') as f:
        # ── En-tête
        f.write(f'# Recherche immobilière — {city}\n\n')
        if notes:
            f.write(f'> {notes}\n\n')
        f.write(f'*Généré le {now}*\n\n')
        f.write('---\n\n')

        # ── Résumé critères
        f.write('## Critères\n\n')
        f.write(f'| Critère | Valeur |\n|---|---|\n')
        f.write(f'| Ville | {city} |\n')
        if city.lower() == 'paris' and postals:
            arr_labels = []
            for p in postals:
                try:
                    arr_labels.append(f'{int(p[3:])}e')
                except Exception:
                    arr_labels.append(p)
            f.write(f'| Arrondissements | {", ".join(arr_labels)} |\n')
        elif postals:
            f.write(f'| Codes postaux | {", ".join(postals)} |\n')
        f.write(f'| Prix max | {max_price} € CC/mois |\n')
        coloc_label = 'Uniquement' if only_coloc else ('Oui' if allow_coloc else 'Non')
        f.write(f'| Colocation | {coloc_label} |\n')
        f.write('\n')

        # ── Stats globales
        f.write('## Statistiques\n\n')
        f.write(f'- **{len(entries)} annonces** au total\n')
        if prices:
            f.write(f'- Prix : {min(prices):.0f} € — {max(prices):.0f} €'
                    f'  |  Moyen : **{mean(prices):.0f} €**  |  Médiane : **{median(prices):.0f} €**\n')

        # Stats par quartier
        by_quartier: dict = defaultdict(list)
        for e in entries:
            by_quartier[e.get('quartier', 'Inconnu')].append(e.get('loyer_cc') or 0)

        if len(by_quartier) > 1:
            f.write('\n### Par quartier / arrondissement\n\n')
            f.write('| Quartier | Nb | Prix moy. | Prix min | Prix max |\n')
            f.write('|---|---:|---:|---:|---:|\n')
            for q, pl in sorted(by_quartier.items(), key=lambda x: -len(x[1])):
                f.write(f'| {q} | {len(pl)} | {mean(pl):.0f} € | {min(pl):.0f} € | {max(pl):.0f} € |\n')
            f.write('\n')

        # ── Table principale
        f.write('## Annonces (prix croissant)\n\n')
        f.write('| # | Titre | Quartier | CC (€) | HC (€) | m² | P. | Dispo | Source |\n')
        f.write('|---:|---|---|---:|---:|---:|---:|---|---|\n')

        for i, e in enumerate(entries_sorted, 1):
            titre = e.get('titre', '').replace('\n', ' ').replace('|', '\\|')
            url = e.get('url', '')
            titre_md = f'[{titre}]({url})' if url else titre
            quartier = e.get('quartier', '')
            cc = f"{e.get('loyer_cc'):.0f}" if isinstance(e.get('loyer_cc'), (int, float)) else ''
            hc = f"{e.get('loyer_hc'):.0f}" if isinstance(e.get('loyer_hc'), (int, float)) else ''
            surface = f"{e.get('surface'):.0f}" if isinstance(e.get('surface'), (int, float)) else ''
            pieces = fmt(e.get('pieces') or '')
            dispo = e.get('disponibilite', '')
            source = e.get('source', '')
            f.write(f'| {i} | {titre_md} | {quartier} | {cc} | {hc} | {surface} | {pieces} | {dispo} | {source} |\n')

    print(f'Généré : {OUT}  ({len(entries)} annonces, ville={city})')


if __name__ == '__main__':
    main()

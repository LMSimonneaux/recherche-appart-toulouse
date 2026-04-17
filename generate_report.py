#!/usr/bin/env python3
"""Génère un rapport markdown riche à partir des annonces collectées.

Format: synthèse + répartition quartier + Liste 1 (prix) + Liste 2 (surface)
+ Top 5 rapport qualité/prix + méthodologie.

Usage:
    python generate_report.py                       # lit results/<city>/annonces.json (selon search_config.json)
    python generate_report.py --city Toulouse       # force une ville
    python generate_report.py --input path/to.json --output path/to.md
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from statistics import mean, median


def load_json(path: str):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_config(path: str = 'search_config.json') -> dict:
    if os.path.exists(path):
        try:
            return load_json(path)
        except Exception:
            pass
    return {}


def city_slug(city: str) -> str:
    return (city or 'recherche').strip().lower().replace(' ', '-')


def postals_to_arr_label(postals: list[str]) -> str:
    parts = []
    for p in postals:
        if p.startswith('750') and len(p) == 5:
            n = int(p[3:])
            parts.append(f'{n}e')
    return ', '.join(parts) if parts else ', '.join(postals)


def fmt_price(v) -> str:
    return f'{v:.0f} €' if isinstance(v, (int, float)) else '—'


def fmt_surface(v) -> str:
    return f'{v:.0f} m²' if isinstance(v, (int, float)) else '—'


def fmt_int(v) -> str:
    return str(int(v)) if isinstance(v, (int, float)) else '—'


def escape_pipe(s: str) -> str:
    return (s or '').replace('\n', ' ').replace('|', '\\|')


_MOIS_FR = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin',
            'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre']


def _date_fr(dt: datetime) -> str:
    return f'{dt.day} {_MOIS_FR[dt.month - 1]} {dt.year}'


def generate(entries: list[dict], cfg: dict, out_path: str):
    today = _date_fr(datetime.now())
    city = cfg.get('city', 'Inconnue')
    max_price = cfg.get('max_price', '?')
    allow_coloc = cfg.get('allow_coloc', False)
    only_coloc = cfg.get('only_coloc', False)
    postals = cfg.get('postal_codes', [])
    available_from = cfg.get('available_from', '')
    notes = cfg.get('notes', '')

    if only_coloc:
        coloc_label = 'colocations uniquement'
    elif allow_coloc:
        coloc_label = 'appartement ou colocation'
    else:
        coloc_label = 'pas de colocation'

    if city.lower() == 'paris' and postals:
        geo_label = f'arrondissements {postals_to_arr_label(postals)}'
    elif city.lower() == 'toulouse':
        geo_label = 'centre-ville' if cfg.get('center_only', True) else 'toute la ville'
    elif postals:
        geo_label = f'codes postaux {", ".join(postals)}'
    else:
        geo_label = city

    prices = [e.get('loyer_cc') for e in entries if isinstance(e.get('loyer_cc'), (int, float))]
    surfaces = [e.get('surface') for e in entries if isinstance(e.get('surface'), (int, float))]

    dispos = [e.get('disponibilite', '') for e in entries]
    imm_count = sum(1 for d in dispos if d in ('Immédiat', '', 'Non précisé'))
    later_count = len(entries) - imm_count

    sources = Counter(e.get('source', '?') for e in entries)
    sources_label = ', '.join(f'{s} ({n})' for s, n in sources.most_common())

    by_quartier: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        q = (e.get('quartier') or 'Inconnu').strip()
        by_quartier[q].append(e)

    by_price = sorted(entries, key=lambda e: (e.get('loyer_cc') is None, e.get('loyer_cc') or 0))
    by_surface = sorted(entries, key=lambda e: -(e.get('surface') or 0))

    def value_ratio(e):
        s = e.get('surface') or 0
        p = e.get('loyer_cc') or 0
        return (p / s) if s > 0 else float('inf')

    def _is_coloc(e):
        t = (e.get('titre', '') or '').lower()
        u = (e.get('url', '') or '').lower()
        return 'coloc' in t or 'co-loc' in t or 'colocation' in u
    # Top qualité/prix : on exclut les colocs (surface = appart total, fausse le €/m²)
    top_value = sorted(
        [e for e in entries
         if (e.get('surface') or 0) > 0
         and (e.get('loyer_cc') or 0) > 0
         and not _is_coloc(e)],
        key=value_ratio,
    )[:5]

    lines: list[str] = []
    w = lines.append

    # ─── Header ─────────────────────────────────────────────────────────
    w(f'# Recherche d\'appartements — {city}')
    w('')
    w(f'> Rapport généré le {today}')
    w(f'> Critères : location, ≤ {max_price} € CC/mois, {geo_label}, {coloc_label}')
    if available_from:
        w(f'> Disponible à partir de : {available_from}')
    if notes:
        w(f'> *{notes}*')
    w('')
    w('---')
    w('')

    # ─── Synthèse ──────────────────────────────────────────────────────
    w('## Synthèse')
    w('')
    w('| Indicateur | Valeur |')
    w('|---|---|')
    w(f'| Annonces valides | **{len(entries)}** |')
    if prices:
        w(f'| Fourchette de prix | {min(prices):.0f} € — {max(prices):.0f} € CC |')
        w(f'| Prix moyen | {mean(prices):.0f} € CC |')
        w(f'| Prix médian | {median(prices):.0f} € CC |')
    if surfaces:
        w(f'| Fourchette de surface | {min(surfaces):.0f} m² — {max(surfaces):.0f} m² |')
        w(f'| Surface moyenne | {mean(surfaces):.0f} m² |')
    w(f'| Disponibles immédiatement | {imm_count} |')
    w(f'| Disponibles prochainement | {later_count} |')
    w(f'| Source(s) | {sources_label or "—"} |')
    w('')

    # ─── Répartition par quartier ──────────────────────────────────────
    if len(by_quartier) > 1:
        w('### Répartition par quartier')
        w('')
        w('| Quartier | Annonces | Prix moy. | Prix min | Prix max |')
        w('|---|---:|---:|---:|---:|')
        for q, items in sorted(by_quartier.items(), key=lambda x: -len(x[1])):
            qp = [e.get('loyer_cc') for e in items if isinstance(e.get('loyer_cc'), (int, float))]
            if qp:
                w(f'| {escape_pipe(q)} | {len(items)} | {mean(qp):.0f} € | {min(qp):.0f} € | {max(qp):.0f} € |')
            else:
                w(f'| {escape_pipe(q)} | {len(items)} | — | — | — |')
        w('')

    w('---')
    w('')

    # ─── Top 5 rapport qualité/prix ────────────────────────────────────
    if top_value:
        w('## Top 5 — Meilleur rapport qualité / prix (€/m²)')
        w('')
        for i, e in enumerate(top_value, 1):
            ratio = value_ratio(e)
            titre = escape_pipe(e.get('titre', ''))
            q = escape_pipe(e.get('quartier', ''))
            cc = e.get('loyer_cc')
            sf = e.get('surface')
            url = e.get('url', '')
            w(f'{i}. **{fmt_surface(sf)} — {fmt_price(cc)}/mois — {q}** ({ratio:.1f} €/m²)')
            w(f'   {titre}')
            w(f'   {url}')
            w('')

        w('---')
        w('')

    # ─── Liste 1 — Par prix croissant ──────────────────────────────────
    w('## Liste 1 — Par prix croissant')
    w('')
    w('| # | Titre | Quartier | Loyer CC | Loyer HC | Surface | Pièces | Disponibilité | Lien |')
    w('|--:|-------|----------|---------:|---------:|--------:|-------:|---------------|------|')
    for i, e in enumerate(by_price, 1):
        titre = escape_pipe((e.get('titre') or '')[:60])
        q = escape_pipe((e.get('quartier') or '')[:30])
        cc = fmt_price(e.get('loyer_cc'))
        hc = fmt_price(e.get('loyer_hc')) if e.get('loyer_hc') else '—'
        sf = fmt_surface(e.get('surface'))
        pcs = fmt_int(e.get('pieces'))
        dispo = escape_pipe(e.get('disponibilite', '') or '—')
        url = e.get('url', '')
        link = f"[Voir l'annonce]({url})" if url else '—'
        w(f'| {i} | {titre} | {q} | {cc} | {hc} | {sf} | {pcs} | {dispo} | {link} |')
    w('')
    w('---')
    w('')

    # ─── Liste 2 — Par surface décroissante ────────────────────────────
    w('## Liste 2 — Par surface décroissante')
    w('')
    w('| # | Titre | Quartier | Surface | Loyer CC | Loyer HC | Pièces | Disponibilité | Lien |')
    w('|--:|-------|----------|--------:|---------:|---------:|-------:|---------------|------|')
    for i, e in enumerate(by_surface, 1):
        titre = escape_pipe((e.get('titre') or '')[:60])
        q = escape_pipe((e.get('quartier') or '')[:30])
        cc = fmt_price(e.get('loyer_cc'))
        hc = fmt_price(e.get('loyer_hc')) if e.get('loyer_hc') else '—'
        sf = fmt_surface(e.get('surface'))
        pcs = fmt_int(e.get('pieces'))
        dispo = escape_pipe(e.get('disponibilite', '') or '—')
        url = e.get('url', '')
        link = f"[Voir l'annonce]({url})" if url else '—'
        w(f'| {i} | {titre} | {q} | {sf} | {cc} | {hc} | {pcs} | {dispo} | {link} |')
    w('')
    w('---')
    w('')

    # ─── Méthodologie ──────────────────────────────────────────────────
    w('## Méthodologie')
    w('')
    w(f"- Collecte automatisée via Playwright (`scraper.py`) — sources tentées : Bien'ici, Leboncoin, PAP, SeLoger.")
    w(f"- Bien'ici : interception des réponses API JSON (endpoint `realEstateAds`) — source principale fiable.")
    w(f"- Leboncoin / SeLoger / Logic-Immo : bloqués par Datadome (403) — non utilisables sans proxy.")
    w(f"- Filtres appliqués : prix ≤ {max_price} €, {geo_label}, {coloc_label}"
      + (f", disponible à partir de {available_from}" if available_from else '')
      + '.')
    w('- Dédoublonnage par URL et par triplet (surface, loyer, pièces).')
    w('- Rapport auto-généré : regénère simplement via `python generate_report.py`.')
    w('')

    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f'✓ Rapport généré : {out_path} ({len(entries)} annonces)')


def main():
    parser = argparse.ArgumentParser(description='Génère un rapport markdown depuis annonces.json')
    parser.add_argument('--city', help='Ville (détermine results/<slug>/)')
    parser.add_argument('--input', help='Chemin annonces.json (écrase --city)')
    parser.add_argument('--output', help='Chemin rapport.md (écrase --city)')
    parser.add_argument('--config', default='search_config.json')
    args = parser.parse_args()

    cfg = load_config(args.config)
    city = args.city or cfg.get('city', 'Toulouse')
    slug = city_slug(city)

    input_path = args.input or f'results/{slug}/annonces.json'
    output_path = args.output or f'results/{slug}/rapport.md'

    if not os.path.exists(input_path):
        print(f'✗ Fichier introuvable : {input_path}', file=sys.stderr)
        sys.exit(1)

    entries = load_json(input_path)
    if not isinstance(entries, list):
        print(f'✗ Format invalide dans {input_path}', file=sys.stderr)
        sys.exit(1)

    generate(entries, cfg, output_path)


if __name__ == '__main__':
    main()

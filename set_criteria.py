#!/usr/bin/env python3
"""Configurer les critères de recherche → `search_config.json`.

Usage interactif:
  python set_criteria.py

Presets prêts à l'emploi:
  python set_criteria.py --preset paris          # Paris arr. 5/6/7/8/11/13/14/15, ≤500€, studio + coloc
  python set_criteria.py --preset paris-coloc    # Idem, colocations seulement
  python set_criteria.py --preset toulouse       # Toulouse centre, ≤600€, sans coloc

Personnalisation rapide:
  python set_criteria.py --preset paris --max-price 600 --run
  python set_criteria.py --city Paris --arrondissements 5,6,11 --max-price 550 --only-coloc --run
  python set_criteria.py --city Toulouse --postal-codes 31000,31400 --max-price 700 --allow-coloc --run
"""
import json
import os
import sys
import argparse
import subprocess

CONFIG_PATH = "search_config.json"

# ─── Presets ─────────────────────────────────────────────────────────

PARIS_DEFAULT_ARRONDISSEMENTS = [5, 6, 7, 8, 11, 13, 14, 15]

PRESETS: dict = {
    'paris': {
        'max_price': 500,
        'city': 'Paris',
        'postal_codes': [f'750{a:02d}' for a in PARIS_DEFAULT_ARRONDISSEMENTS],
        'allow_coloc': True,
        'only_coloc': False,
        'center_only': True,
        'max_pages': 11,
        'notes': f"Paris — arrondissements {', '.join(str(a) + 'e' for a in PARIS_DEFAULT_ARRONDISSEMENTS)}, studio ou coloc",
    },
    'paris-coloc': {
        'max_price': 500,
        'city': 'Paris',
        'postal_codes': [f'750{a:02d}' for a in PARIS_DEFAULT_ARRONDISSEMENTS],
        'allow_coloc': True,
        'only_coloc': True,
        'center_only': True,
        'max_pages': 11,
        'notes': f"Paris — arrondissements {', '.join(str(a) + 'e' for a in PARIS_DEFAULT_ARRONDISSEMENTS)}, colocations uniquement",
    },
    'toulouse': {
        'max_price': 600,
        'city': 'Toulouse',
        'postal_codes': ['31000'],
        'allow_coloc': False,
        'only_coloc': False,
        'center_only': True,
        'max_pages': 11,
        'notes': 'Toulouse centre-ville, ≤600€, sans colocation',
    },
}

# ─── Helpers ─────────────────────────────────────────────────────────

def load_config(path=CONFIG_PATH):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(cfg, path=CONFIG_PATH):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def prompt(text, default):
    try:
        raw = input(f"  {text} [{default}]: ").strip()
    except EOFError:
        return str(default)
    return raw if raw else str(default)


def yn(text, default_yes=False):
    hint = "Y/n" if default_yes else "y/N"
    try:
        raw = input(f"  {text} ({hint}): ").strip().lower()
    except EOFError:
        return default_yes
    if not raw:
        return default_yes
    return raw in ("y", "yes", "o", "oui")


def arrondissements_to_postals(arr_str: str) -> list[str]:
    """Convertit '5,6,11,14' → ['75005', '75006', '75011', '75014']."""
    result = []
    for part in arr_str.split(','):
        part = part.strip().rstrip('e').rstrip('è').rstrip('ème')
        try:
            n = int(part)
            if 1 <= n <= 20:
                result.append(f'750{n:02d}')
        except ValueError:
            pass
    return result


def postals_to_arr_label(postals: list[str]) -> str:
    """['75005','75011'] → '5e, 11e'"""
    parts = []
    for p in postals:
        if p.startswith('750') and len(p) == 5:
            n = int(p[3:])
            parts.append(f'{n}e')
    return ', '.join(parts) if parts else ', '.join(postals)


_MONTH_FR = {
    'janvier': 1, 'jan': 1,
    'février': 2, 'fevrier': 2, 'fev': 2,
    'mars': 3,
    'avril': 4, 'avr': 4,
    'mai': 5,
    'juin': 6,
    'juillet': 7, 'juil': 7,
    'août': 8, 'aout': 8,
    'septembre': 9, 'sept': 9, 'sep': 9,
    'octobre': 10, 'oct': 10,
    'novembre': 11, 'nov': 11,
    'décembre': 12, 'decembre': 12, 'dec': 12,
}


def normalize_available_from(raw: str) -> str:
    """Normalise une date en YYYY-MM-DD.
    Accepte: '2026-09-01', '2026-09', 'septembre 2026', 'sept 2026', '09/2026'.
    Retourne '' si non reconnu.
    """
    if not raw:
        return ''
    raw = raw.strip().lower()
    import re as _re, datetime as _dt
    # Format ISO déjà complet
    m = _re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})', raw)
    if m:
        return raw
    # YYYY-MM
    m = _re.fullmatch(r'(\d{4})-(\d{2})', raw)
    if m:
        return f'{m.group(1)}-{m.group(2)}-01'
    # MM/YYYY
    m = _re.fullmatch(r'(\d{1,2})/(\d{4})', raw)
    if m:
        return f'{m.group(2)}-{int(m.group(1)):02d}-01'
    # "septembre 2026" or "sept 2026"
    m = _re.fullmatch(r'([a-zéûôàè]+)\s*(\d{4})?', raw)
    if m:
        month_str = m.group(1)
        year = int(m.group(2)) if m.group(2) else _dt.date.today().year
        # Auto-advance year if month has already passed
        month_num = _MONTH_FR.get(month_str)
        if month_num:
            today = _dt.date.today()
            if not m.group(2) and (year < today.year or (year == today.year and month_num <= today.month)):
                year = today.year + 1
            return f'{year}-{month_num:02d}-01'
    return ''


# ─── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Créer / modifier search_config.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # Preset
    parser.add_argument(
        '--preset', choices=list(PRESETS.keys()),
        help=f"Profil prédéfini : {', '.join(PRESETS.keys())}"
    )
    # Critères manuels
    parser.add_argument('--max-price', type=int, help='Prix max (€ CC/mois)')
    parser.add_argument('--city', type=str, help='Ville  (ex: Paris, Toulouse)')
    parser.add_argument(
        '--arrondissements', type=str,
        help='Paris — numéros d\'arrondissements séparés par des virgules : 5,6,11,14'
    )
    parser.add_argument('--postal-codes', type=str, help='Codes postaux séparés par des virgules (alternative à --arrondissements)')
    parser.add_argument('--allow-coloc', dest='allow_coloc', action='store_true', help='Accepter les annonces en colocation')
    parser.add_argument('--only-coloc', dest='only_coloc', action='store_true', help='Ne garder que les colocations')
    parser.add_argument('--center-only', dest='center_only', action='store_true', help='Limiter au centre / arrondissements configurés')
    parser.add_argument('--no-center-only', dest='center_only', action='store_false', help='Accepter toute la ville')
    parser.add_argument('--max-pages', type=int, help='Nb max de pages par source')
    parser.add_argument('--notes', type=str, help='Note libre sur cette recherche')
    parser.add_argument(
        '--available-from', dest='available_from', type=str,
        help='Date de disponibilité minimum (ex: "septembre 2026", "2026-09", "2026-09-01"). Laisser vide pour désactiver.'
    )
    parser.add_argument('--run', action='store_true', help='Lancer scraper.py après sauvegarde')
    parser.set_defaults(center_only=None)
    args = parser.parse_args()

    # ── Étape 1 : partir du preset ou de la config existante
    if args.preset:
        cfg = PRESETS[args.preset].copy()
        print(f"\n  Preset chargé : {args.preset}")
        print(f"  → {cfg.get('notes', '')}\n")
    else:
        cfg = load_config()

    # ── Étape 2 : appliquer les overrides CLI
    if args.max_price is not None:
        cfg['max_price'] = args.max_price
    if args.city is not None:
        cfg['city'] = args.city
    if args.arrondissements is not None:
        cfg['postal_codes'] = arrondissements_to_postals(args.arrondissements)
    elif args.postal_codes is not None:
        cfg['postal_codes'] = [p.strip() for p in args.postal_codes.split(',') if p.strip()]
    if args.allow_coloc:
        cfg['allow_coloc'] = True
    if args.only_coloc:
        cfg['only_coloc'] = True
    if args.center_only is not None:
        cfg['center_only'] = args.center_only
    if args.max_pages is not None:
        cfg['max_pages'] = args.max_pages
    if args.notes is not None:
        cfg['notes'] = args.notes
    if args.available_from is not None:
        cfg['available_from'] = normalize_available_from(args.available_from)

    # ── Étape 3 : mode interactif si pas de preset ET pas de flag CLI
    if not args.preset and not any([
        args.max_price, args.city, args.arrondissements,
        args.postal_codes, args.allow_coloc, args.only_coloc,
    ]):
        print("\n╔══════════════════════════════════════════════════╗")
        print("║         Recherche appartements — Critères         ║")
        print("╚══════════════════════════════════════════════════╝\n")

        print("  Presets disponibles :")
        for k, v in PRESETS.items():
            print(f"    {k:15s} — {v.get('notes', '')}")
        preset_choice = prompt("Preset à utiliser (laisser vide pour personnaliser)", "")
        if preset_choice in PRESETS:
            cfg = PRESETS[preset_choice].copy()
            print(f"  → Preset '{preset_choice}' chargé.\n")
        else:
            print()
            city = prompt("Ville", cfg.get('city', 'Toulouse'))
            cfg['city'] = city

            if city.lower() == 'paris':
                current_arr = postals_to_arr_label(cfg.get('postal_codes', []))
                arr_raw = prompt(
                    "Arrondissements (ex: 5,6,7,8,11,13,14,15)",
                    current_arr or "5,6,7,8,11,13,14,15"
                )
                cfg['postal_codes'] = arrondissements_to_postals(arr_raw)
            else:
                default_pc = ','.join(cfg.get('postal_codes', ['31000']))
                pc_raw = prompt("Codes postaux (séparés par ',')", default_pc)
                cfg['postal_codes'] = [p.strip() for p in pc_raw.split(',') if p.strip()]

            cfg['max_price'] = int(prompt("Prix max (€/mois, charges comprises)", cfg.get('max_price', 600)))
            cfg['allow_coloc'] = yn("Autoriser la colocation ?", cfg.get('allow_coloc', False))
            cfg['only_coloc'] = yn("Ne garder QUE les colocations ?", cfg.get('only_coloc', False))
            cfg['center_only'] = yn("Limiter aux quartiers/arrondissements configurés ?", cfg.get('center_only', True))
            cfg['max_pages'] = int(prompt("Pages max par source", cfg.get('max_pages', 11)))
            avail_raw = prompt("Disponible à partir de (ex: septembre 2026, laisser vide pour tout)", cfg.get('available_from', '') or '')
            cfg['available_from'] = normalize_available_from(avail_raw)
            note = prompt("Note sur cette recherche (optionnel)", cfg.get('notes', ''))
            if note:
                cfg['notes'] = note

    # ── Étape 4 : normaliser et sauvegarder
    new_cfg = {
        'max_price':      int(cfg.get('max_price', 600)),
        'city':           cfg.get('city', 'Toulouse'),
        'postal_codes':   cfg.get('postal_codes', ['31000']),
        'allow_coloc':    bool(cfg.get('allow_coloc', False)),
        'only_coloc':     bool(cfg.get('only_coloc', False)),
        'center_only':    bool(cfg.get('center_only', True)),
        'max_pages':      int(cfg.get('max_pages', 11)),
        'notes':          cfg.get('notes', ''),
        'available_from': cfg.get('available_from', ''),
    }

    save_config(new_cfg)
    print("\n  Config sauvegardée :")
    print(f"    Ville      : {new_cfg['city']}")
    if new_cfg['city'].lower() == 'paris':
        print(f"    Arrondiss. : {postals_to_arr_label(new_cfg['postal_codes'])}")
    else:
        print(f"    Codes post.: {', '.join(new_cfg['postal_codes'])}")
    print(f"    Prix max   : {new_cfg['max_price']} €/mois CC")
    coloc_label = 'uniquement' if new_cfg['only_coloc'] else ('oui' if new_cfg['allow_coloc'] else 'non')
    print(f"    Colocation : {coloc_label}")
    print(f"    Filtre géo : {'oui' if new_cfg['center_only'] else 'non'}")
    if new_cfg['notes']:
        print(f"    Note       : {new_cfg['notes']}")
    if new_cfg['available_from']:
        print(f"    Dispo à partir de : {new_cfg['available_from']}")
    print()

    if args.run:
        print("  Lancement de scraper.py...")
        proc = subprocess.run([sys.executable, 'scraper.py'])
        sys.exit(proc.returncode)


if __name__ == '__main__':
    main()

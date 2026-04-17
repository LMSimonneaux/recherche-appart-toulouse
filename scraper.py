#!/usr/bin/env python3
"""
Scraper d'annonces immobilières - Toulouse centre-ville ≤ 600€ CC
v2 — Corrections: parsing Bien'ici, filtrage PAP, pagination, exclusion colocations
"""

import json
import os
import re
import sys
import time
import hashlib
from dataclasses import dataclass, asdict
from typing import Optional
from urllib.parse import urljoin, urlparse, parse_qs
import pandas as pd
from playwright.sync_api import sync_playwright, BrowserContext, Response

# ─── Modèle de données ───────────────────────────────────────────────

@dataclass
class Annonce:
    titre: str
    url: str
    loyer_cc: Optional[float] = None
    loyer_hc: Optional[float] = None
    surface: Optional[float] = None
    pieces: Optional[int] = None
    quartier: str = ""
    disponibilite: str = "Non précisé"
    source: str = ""

    @property
    def dedup_key(self):
        key = f"{self.surface or 0:.0f}_{self.loyer_cc or 0:.0f}_{self.pieces or 0}"
        return key


# ─── Filtres centre-ville ─────────────────────────────────────────────

# Quartiers centre-ville de Toulouse (coeur + immédiatement adjacents)
CENTRE_QUARTIERS = [
    # Coeur historique
    'capitole', 'saint-étienne', 'saint étienne', 'st-étienne', 'st étienne',
    'saint-etienne', 'saint etienne',
    'carmes', 'les carmes', 'carmes-esquirol', 'carmes esquirol',
    'saint-cyprien', 'saint cyprien', 'st-cyprien', 'st cyprien',
    'jean-jaurès', 'jean jaurès', 'jean-jaures', 'jean jaures',
    'esquirol', 'françois verdier', 'francois verdier', 'françois-verdier',
    'alsace-lorraine', 'alsace lorraine', 'wilson', "jeanne d'arc",
    'daurade', 'la daurade', 'saint-pierre', 'saint pierre', 'st-pierre',
    'matabiau', 'bayard', 'arnaud bernard', 'arnaud-bernard',
    'centre', 'centre-ville', 'centre ville', 'hypercentre', 'hyper-centre',
    'boulingrin', 'colombette', 'la colombette',
    'ozenne', 'saint-aubin', 'saint aubin', 'st-aubin',
    'compans', 'compans-caffarelli', 'compans caffarelli',
    'pont-neuf', 'pont neuf', 'saint-georges', 'saint georges',
    'filatiers', 'dalbade', 'la dalbade',
    'saint-rome', 'saint rome',
    # Immédiatement adjacents au centre
    'saint-sernin', 'saint sernin', 'st-sernin',
    'châlets', 'chalets', 'les châlets', 'les chalets',
    'saint-michel', 'saint michel', 'st-michel',
    'busca', 'jardin des plantes', 'port saint-sauveur', 'port saint sauveur',
    'patte d\'oie', 'patte-d\'oie', 'patte d oie',
    'ponts jumeaux', 'ponts-jumeaux',
    'croix de pierre', 'croix-de-pierre',
    'guilhemery', 'guilheméry', 'bonhoure',
]

# Quartiers explicitement hors centre
HORS_CENTRE = [
    'colomiers', 'blagnac', 'tournefeuille', 'ramonville', 'castanet',
    'balma', 'aucamville', 'fenouillet', 'launaguet', "l'union",
    'cugnaux', 'portet', 'muret', 'saint-orens', 'labège',
    'rangueil', 'empalot', 'mirail', 'reynerie', 'bellefontaine',
    'borderouge', 'croix-daurade', 'croix daurade', 'lalande', 'ginestous',
    'montaudran', 'lardenne', 'cépière', 'purpan', 'casselardit',
    'ancely', 'fontaine-lestang', 'fontaine lestang',
    'bagatelle', 'faourette', 'papus', 'tabar', 'lafourguette',
    'langlade', 'pouvourville', 'pech david', 'côte pavée', 'cote pavee',
    'sept deniers', 'trois cocus',
    'négreneys', 'mazades', 'gramont',
    'saint-agne', 'saint agne', 'st-agne',
    'saint-simon', 'saint simon', 'st-simon',
    'saint-martin', 'saint martin', 'st-martin-du-touch',
    'jolimont', 'soupetard',
    'la cépière', 'la cepiere',
    'pradettes', 'les pradettes',
    'basso cambo', 'argoulets', 'les argoulets',
    'saouzelong', 'limayrac', 'château de l\'hers', 'chateau de l\'hers',
    'ormeau', 'la terrasse', 'grande plaine',
    'la vache', 'la salade',
    'lafourguette', 'gironis',
    'les récollets', 'les recollets',
]


# ─── Données arrondissements Paris ────────────────────────────────────

# Mots-clés associés à chaque code postal parisien
PARIS_ARRONDISSEMENT_KEYWORDS: dict[str, list[str]] = {
    '75001': ['1er', '1ème', '1eme', 'louvre', 'châtelet', 'chatelet', 'halles'],
    '75002': ['2e', '2ème', '2eme', 'bourse', 'sentier'],
    '75003': ['3e', '3ème', '3eme', 'temple', 'marais'],
    '75004': ['4e', '4ème', '4eme', 'marais', 'île de la cité', 'ile de la cite', 'saint-gervais'],
    '75005': ['5e', '5ème', '5eme', 'latin', 'panthéon', 'pantheon', 'mouffetard', 'jussieu'],
    '75006': ['6e', '6ème', '6eme', 'luxembourg', 'saint-germain', 'odéon', 'odeon', 'mabillon'],
    '75007': ['7e', '7ème', '7eme', 'invalides', 'eiffel', 'palais bourbon', 'rue de grenelle'],
    '75008': ['8e', '8ème', '8eme', 'champs', 'madeleine', 'europe', 'étoile', 'etoile'],
    '75009': ['9e', '9ème', '9eme', 'opéra', 'opera', 'pigalle', 'anvers'],
    '75010': ['10e', '10ème', '10eme', 'gare du nord', "gare de l'est", 'canal saint-martin', 'strasbourg saint-denis'],
    '75011': ['11e', '11ème', '11eme', 'bastille', 'oberkampf', 'ménilmontant', 'menilmontant', 'voltaire', 'charonne'],
    '75012': ['12e', '12ème', '12eme', 'nation', 'bercy', 'gare de lyon', 'reuilly'],
    '75013': ['13e', '13ème', '13eme', 'italie', 'gobelins', 'olympiades', 'tolbiac', 'maison-blanche'],
    '75014': ['14e', '14ème', '14eme', 'montparnasse', 'denfert', 'alésia', 'alesia', 'plaisance', 'pernety'],
    '75015': ['15e', '15ème', '15eme', 'convention', 'vaugirard', 'grenelle', 'javel', 'beaugrenelle'],
    '75016': ['16e', '16ème', '16eme', 'passy', 'trocadéro', 'trocadero', 'auteuil', 'muette'],
    '75017': ['17e', '17ème', '17eme', 'batignolles', 'monceau', 'épinettes', 'epinettes', 'wagram'],
    '75018': ['18e', '18ème', '18eme', 'montmartre', 'clignancourt', 'la chapelle', 'goutte d\'or'],
    '75019': ['19e', '19ème', '19eme', 'buttes chaumont', 'ourcq', 'stalingrad', 'crimée'],
    '75020': ['20e', '20ème', '20eme', "père lachaise", 'belleville', 'gambetta'],
}

# Numéro → code postal
PARIS_ARR_TO_POSTAL: dict[int, str] = {i: f'750{i:02d}' for i in range(1, 21)}


def is_valid_paris(quartier: str, postal_code: str = "") -> bool:
    """Vérifie si l'annonce est dans les arrondissements de Paris configurés."""
    allowed = CONFIG.get('postal_codes', [])
    # Pas de restriction → on accepte tout Paris
    if not allowed:
        return '750' in (postal_code or '') or 'paris' in quartier.lower()
    # Match direct par code postal (5 chiffres)
    if postal_code and postal_code in allowed:
        return True
    # Chercher un code postal 75xxx dans le texte du quartier
    pc_m = re.search(r'75(\d{3})', quartier)
    if pc_m:
        detected = f'75{pc_m.group(1)}'
        return detected in allowed
    # Match par mots-clés avec word boundaries pour éviter '6e' dans '16e'
    text = quartier.lower()
    for postal in allowed:
        for kw in PARIS_ARRONDISSEMENT_KEYWORDS.get(postal, []):
            # Word boundary: on cherche le mot entier, pas une sous-chaîne
            if re.search(r'(?<!\d)' + re.escape(kw) + r'(?!\w)', text):
                return True
    return False


def is_valid_location(quartier: str, postal_code: str = "") -> bool:
    """Dispatcher : valide la localisation selon la ville configurée."""
    city = CONFIG.get('city', 'Toulouse').lower()
    if city == 'paris':
        return is_valid_paris(quartier, postal_code)
    return is_centre_ville(quartier, postal_code)


def is_centre_ville(quartier: str, postal_code: str = "") -> bool:
    """Vérifie si l'annonce est dans le centre-ville de Toulouse."""
    text = f"{quartier}".lower()

    # D'abord: vérifier si un quartier centre est explicitement mentionné
    # (priorité sur le code postal car certains quartiers centre sont en 31300/31400/31500)
    if any(kw in text for kw in CENTRE_QUARTIERS):
        # Mais vérifier qu'il n'y a pas aussi un quartier hors centre (faux positif)
        if not any(kw in text for kw in HORS_CENTRE):
            return True

    # Exclusion quartiers hors centre
    if any(kw in text for kw in HORS_CENTRE):
        return False

    # Code postal seul: seul 31000 est accepté par défaut
    if postal_code and postal_code not in ('31000', ''):
        return False

    # Code postal 31000 sans quartier identifiable = probablement centre
    if postal_code == '31000':
        return True

    return False


def parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    text = text.replace('\xa0', '').replace(' ', '').replace('€', '').replace(',', '.')
    match = re.search(r'(\d+(?:\.\d+)?)', text)
    return float(match.group(1)) if match else None


def parse_surface(text: str) -> Optional[float]:
    if not text:
        return None
    match = re.search(r'(\d+(?:[.,]\d+)?)\s*m[²2]?', text.replace('\xa0', ''))
    return float(match.group(1).replace(',', '.')) if match else None


def parse_pieces(text: str) -> Optional[int]:
    if not text:
        return None
    match = re.search(r'(\d+)\s*(?:pièce|piece|p\.)', text.lower())
    if match:
        return int(match.group(1))
    match = re.search(r'[TtFf](\d+)', text)
    if match:
        return int(match.group(1))
    if 'studio' in text.lower():
        return 1
    return None


# ─── Configuration (modifiable via `search_config.json`) ───────────────
DEFAULT_CONFIG = {
    "max_price": 600,
    "city": "Toulouse",
    "postal_codes": ["31000"],
    "allow_coloc": False,
    "only_coloc": False,
    "center_only": True,
    "max_pages": 11,
    "notes": "",
    "available_from": ""
}


def load_config(path: str = "search_config.json"):
    cfg = DEFAULT_CONFIG.copy()
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                user = json.load(f)
                if isinstance(user, dict):
                    cfg.update(user)
    except Exception:
        pass
    return cfg


CONFIG = load_config()


# ─── Scraper Bien'ici (API interception avec pagination) ──────────────

def _accept_cookies(page, selectors=None):
    """Tente d'accepter les cookies via plusieurs sélecteurs."""
    defaults = [
        'button:has-text("Tout accepter")',
        'button:has-text("Accepter")',
        '#didomi-notice-agree-button',
        'button[id*=accept]',
        '[aria-label*="accepter" i]',
    ]
    for sel in selectors or defaults:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(1500)
                return True
        except Exception:
            continue
    return False


def scrape_bienici(context: BrowserContext) -> list[Annonce]:
    """Scrape Bien'ici via interception des appels API avec pagination."""
    print("\n🔍 Scraping Bien'ici...")
    annonces = []
    all_ads_raw = []
    total_count = 0
    cookies_accepted = False

    # Paginer pour couvrir un maximum d'annonces
    for page_num in range(1, CONFIG.get('max_pages', 11) + 1):
        api_responses = []
        page = context.new_page()

        def make_interceptor(responses_list):
            def intercept(response: Response):
                try:
                    url = response.url
                    ct = response.headers.get('content-type', '')
                    # Capturer les réponses JSON potentiellement utiles
                    if 'json' in ct or url.endswith('.json') or 'realEstate' in url or 'search' in url:
                        try:
                            body = response.json()
                            responses_list.append(body)
                        except Exception:
                            pass
                except Exception:
                    pass
            return intercept

        page.on('response', make_interceptor(api_responses))

        city_slug = CONFIG.get('city', 'Toulouse').lower().replace(' ', '-')
        postals = CONFIG.get('postal_codes', ['31000']) or ['31000']
        # Bien'ici accepte plusieurs locations séparées par des virgules
        if len(postals) > 1:
            location = ','.join(f"{city_slug}-{p}" for p in postals)
        else:
            location = f"{city_slug}-{postals[0]}" if postals else city_slug
        # Type de bien : coloc seule, les deux, ou appart seulement
        if CONFIG.get('only_coloc'):
            type_param = 'typeBien[]=colocation'
        elif CONFIG.get('allow_coloc'):
            type_param = 'typeBien[]=appartement&typeBien[]=colocation'
        else:
            type_param = 'typeBien[]=appartement'
        url = f'https://www.bienici.com/recherche/location/{location}?prix-max={CONFIG.get("max_price", 600)}&{type_param}&page={page_num}'
        try:
            print(f"  → Page {page_num}...")
            page.goto(url, wait_until='domcontentloaded', timeout=30000)
            page.wait_for_timeout(3500)

            # Accepter les cookies uniquement à la première page (session partagée via context)
            if not cookies_accepted:
                if _accept_cookies(page):
                    cookies_accepted = True
                    page.wait_for_timeout(2000)

            # Scroll pour déclencher les requêtes API
            for _ in range(4):
                page.evaluate('window.scrollBy(0, 800)')
                page.wait_for_timeout(700)

            page.wait_for_timeout(2500)

        except Exception as e:
            print(f"  ⚠ Erreur navigation page {page_num}: {e}")

        page.close()

        for data in api_responses:
            if isinstance(data, dict) and 'realEstateAds' in data:
                if total_count == 0:
                    total_count = data.get('total', 0)
                    print(f"  → Total annonces sur Bien'ici: {total_count}")
                ads = data.get('realEstateAds', [])
                all_ads_raw.extend(ads)

        # Si on a tout récupéré ou rien intercepté de pertinent, arrêter
        if len(all_ads_raw) >= max(total_count, 1) or total_count == 0:
            break

    print(f"  → {len(all_ads_raw)} annonces brutes récupérées")

    # Parser chaque annonce
    seen_ids = set()
    for ad in all_ads_raw:
        if not isinstance(ad, dict):
            continue
        try:
            ad_id = ad.get('id', '')
            if ad_id in seen_ids:
                continue
            seen_ids.add(ad_id)

            titre = ad.get('title', '') or ''
            is_coloc = bool(ad.get('flatSharing', False)) or 'coloc' in titre.lower() or 'co-loc' in titre.lower() or 'colocation' in titre.lower()

            # If only_coloc is requested, keep only colocations
            if CONFIG.get('only_coloc', False):
                if not is_coloc:
                    continue
            else:
                # Otherwise, exclude colocations unless allow_coloc is True
                if is_coloc and not CONFIG.get('allow_coloc', False):
                    continue

            price = ad.get('price')
            if not price or float(price) > CONFIG.get('max_price', 600):
                continue

            rent_hc = ad.get('rentWithoutCharges')
            charges = ad.get('charges')
            surface = ad.get('surfaceArea')
            rooms = ad.get('roomsQuantity')

            # Quartier
            district = ad.get('district', {})
            if isinstance(district, dict):
                quartier_name = district.get('libelle', district.get('name', ''))
            else:
                quartier_name = str(district) if district else ''

            city = ad.get('city', '')
            postal_code = ad.get('postalCode', '')

            # Disponibilité
            avail_date = ad.get('availableDate', '')
            if avail_date:
                dispo = avail_date[:10]  # YYYY-MM-DD
            else:
                dispo = "Immédiat"

            # URL
            # Format Bien'ici: /annonce/location/<city>-<postal>/ID
            city_slug_for_url = CONFIG.get('city', 'Toulouse').lower().replace(' ', '-')
            ad_url = f"https://www.bienici.com/annonce/location/{city_slug_for_url}-{postal_code}/{ad_id}"

            quartier_display = f"{quartier_name}" if quartier_name else f"{city} ({postal_code})"

            annonces.append(Annonce(
                titre=titre or f"Appartement {surface}m² {quartier_name}",
                url=ad_url,
                loyer_cc=float(price),
                loyer_hc=float(rent_hc) if rent_hc else None,
                surface=float(surface) if surface else None,
                pieces=int(rooms) if rooms else None,
                quartier=quartier_display,
                disponibilite=dispo,
                source="Bien'ici",
            ))

        except Exception as e:
            print(f"  ⚠ Erreur parsing: {e}")

    print(f"  ✓ Bien'ici: {len(annonces)} annonces (après prix ≤ {CONFIG.get('max_price', 600)}€ et filtres coloc)")
    return annonces


# ─── Scraper Leboncoin (Playwright + __NEXT_DATA__ + HTML) ────────────

def scrape_leboncoin(context: BrowserContext) -> list[Annonce]:
    """Scrape Leboncoin - multiples stratégies."""
    print("\n🔍 Scraping Leboncoin...")
    annonces = []
    api_data = []

    page = context.new_page()

    # Intercepter les réponses API
    def intercept(response: Response):
        url = response.url
        if any(k in url for k in ['finder/search', 'api.leboncoin', '/search']):
            try:
                ct = response.headers.get('content-type', '')
                if 'json' in ct:
                    body = response.json()
                    api_data.append(body)
                    print(f"  [API] {url[:60]}...")
            except Exception:
                pass

    page.on('response', intercept)

    loc_city = CONFIG.get('city', 'Toulouse').replace(' ', '_')
    loc_postal = CONFIG.get('postal_codes', ['31000'])[0] if CONFIG.get('postal_codes') else ''
    loc_part = f"{loc_city}_{loc_postal}" if loc_postal else loc_city
    search_url = f'https://www.leboncoin.fr/recherche?category=10&locations={loc_part}&real_estate_type=2&price=max-{CONFIG.get("max_price", 600)}'

    try:
        page.goto(search_url, wait_until='domcontentloaded', timeout=30000)
        page.wait_for_timeout(6000)

        # Accepter cookies si popup
        try:
            cookie_btn = page.query_selector('#didomi-notice-agree-button, button[id*="accept"]')
            if cookie_btn:
                cookie_btn.click()
                page.wait_for_timeout(2000)
        except Exception:
            pass

        # Scroll
        for _ in range(5):
            page.evaluate('window.scrollBy(0, 500)')
            page.wait_for_timeout(800)

        # Stratégie 1: API interceptée
        for data in api_data:
            ads = data.get('ads', data.get('results', []))
            if isinstance(ads, list):
                for ad in ads:
                    try:
                        a = parse_lbc_ad(ad)
                        if a:
                            annonces.append(a)
                    except Exception:
                        pass

        # Stratégie 2: __NEXT_DATA__
        if not annonces:
            print("  → Tentative __NEXT_DATA__...")
            next_data = page.evaluate('''() => {
                const el = document.querySelector('#__NEXT_DATA__');
                return el ? el.textContent : null;
            }''')

            if next_data:
                try:
                    data = json.loads(next_data)
                    raw_ads = find_nested_ads(data)
                    print(f"  → Trouvé {len(raw_ads)} annonces dans __NEXT_DATA__")
                    for ad in raw_ads:
                        a = parse_lbc_ad(ad)
                        if a:
                            annonces.append(a)
                except Exception as e:
                    print(f"  ⚠ Erreur __NEXT_DATA__: {e}")

        # Stratégie 3: HTML brut
        if not annonces:
            print("  → Tentative HTML parsing...")
            # Chercher tous les liens vers des annonces
            links = page.evaluate('''() => {
                const results = [];
                document.querySelectorAll('a[href]').forEach(a => {
                    const href = a.href;
                    if (href.includes('/ad/') || href.includes('/locations/')) {
                        const text = a.innerText || '';
                        results.push({href: href, text: text.substring(0, 200)});
                    }
                });
                return results;
            }''')
            print(f"  → Trouvé {len(links)} liens d'annonces")

            for link_data in links:
                try:
                    url = link_data['href']
                    text = link_data['text']
                    if not text.strip() or len(text.strip()) < 10:
                        continue

                    price = parse_price(text)
                    surface = parse_surface(text)
                    pieces_n = parse_pieces(text)

                    if price and price <= 600:
                        annonces.append(Annonce(
                            titre=text[:60].strip(),
                            url=url,
                            loyer_cc=price,
                            surface=surface,
                            pieces=pieces_n,
                            quartier="Toulouse (31000)",
                            source="Leboncoin"
                        ))
                except Exception:
                    pass

    except Exception as e:
        print(f"  ⚠ Erreur Leboncoin: {e}")

    page.close()
    print(f"  ✓ Leboncoin: {len(annonces)} annonces trouvées")
    return annonces


def parse_lbc_ad(ad: dict) -> Optional[Annonce]:
    """Parse une annonce Leboncoin depuis un dict API/JSON."""
    if not isinstance(ad, dict):
        return None

    titre = ad.get('subject', ad.get('title', ''))
    ad_id = ad.get('list_id', ad.get('id', ''))
    slug = ad.get('url', '')

    if slug:
        ad_url = f"https://www.leboncoin.fr{slug}" if slug.startswith('/') else slug
    elif ad_id:
        ad_url = f"https://www.leboncoin.fr/ad/locations/{ad_id}"
    else:
        return None

    # Prix
    price = None
    price_data = ad.get('price', [])
    if isinstance(price_data, list) and price_data:
        price = float(price_data[0])
    elif isinstance(price_data, (int, float)):
        price = float(price_data)

    if not price or price > CONFIG.get('max_price', 600):
        return None

    # Detect colocation in Leboncoin ad
    titre_lower = (titre or '').lower()
    is_coloc = ad.get('flatSharing', False) or 'coloc' in titre_lower or 'colocation' in titre_lower or 'co-loc' in titre_lower

    if CONFIG.get('only_coloc', False):
        if not is_coloc:
            return None
    else:
        if is_coloc and not CONFIG.get('allow_coloc', False):
            return None

    # Location
    loc = ad.get('location', {})
    city = loc.get('city', '') if isinstance(loc, dict) else ''
    zipcode = loc.get('zipcode', '') if isinstance(loc, dict) else ''
    quartier = f"{city} ({zipcode})" if city else "Toulouse"

    # Vérifier que c'est bien Toulouse
    if city and 'toulouse' not in city.lower():
        return None

    # Attributs
    surface = None
    pieces = None
    for attr in ad.get('attributes', []):
        key = attr.get('key', '')
        val = attr.get('value', '')
        if key == 'square' and val:
            surface = float(val)
        elif key == 'rooms' and val:
            pieces = int(val)

    return Annonce(
        titre=titre,
        url=ad_url,
        loyer_cc=price,
        surface=surface,
        pieces=pieces,
        quartier=quartier,
        source="Leboncoin"
    )


def find_nested_ads(obj, depth=0):
    """Cherche récursivement les listes d'annonces dans un objet JSON."""
    if depth > 15:
        return []
    results = []
    if isinstance(obj, dict):
        if 'ads' in obj and isinstance(obj['ads'], list):
            results.extend(obj['ads'])
        if 'list_id' in obj and ('subject' in obj or 'title' in obj):
            results.append(obj)
        for v in obj.values():
            results.extend(find_nested_ads(v, depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(find_nested_ads(item, depth + 1))
    return results


# ─── Scraper PAP ──────────────────────────────────────────────────────

def scrape_pap(context: BrowserContext) -> list[Annonce]:
    """Scrape PAP.fr — filtrage strict Toulouse."""
    print("\n🔍 Scraping PAP...")
    annonces = []

    page = context.new_page()

    # Générer des URLs PAP — une URL par code postal/arrondissement
    city_slug = CONFIG.get('city', 'toulouse').lower().replace(' ', '-')
    postals = CONFIG.get('postal_codes', ['31000']) or ['31000']
    price_seg = f"jusqu-a-{CONFIG.get('max_price', 600)}-euros"
    # Pour chaque arrondissement, essaie le format g439 (fonctionne pour toutes les villes)
    urls_to_try = []
    for postal in postals:
        urls_to_try.append(f'https://www.pap.fr/annonce/location-appartement-{city_slug}-{postal}-g439-{price_seg}')
    # Fallback URL générale sans arrondissement
    urls_to_try.append(f'https://www.pap.fr/annonce/locations-appartement-{city_slug}-{price_seg}')

    for search_url in urls_to_try:
        try:
            page.goto(search_url, wait_until='domcontentloaded', timeout=30000)
            page.wait_for_timeout(3000)

            # Cookies
            try:
                btn = page.query_selector('#didomi-notice-agree-button, button[id*="accept"]')
                if btn:
                    btn.click()
                    page.wait_for_timeout(1000)
            except Exception:
                pass

            # Extraire toutes les données de la page via JS
            results = page.evaluate('''() => {
                const items = [];
                // Chercher les liens d'annonces
                const links = document.querySelectorAll('a[href*="/annonces/"]');
                links.forEach(a => {
                    const href = a.getAttribute('href') || '';
                    const text = a.innerText || '';
                    const parent = a.closest('.search-list-item, article, li, .item');
                    const parentText = parent ? parent.innerText : text;
                    items.push({
                        href: href,
                        text: parentText.substring(0, 500),
                        linkText: text.substring(0, 200)
                    });
                });
                return items;
            }''')

            print(f"  → {search_url.split('/')[-1]}: {len(results)} liens trouvés")

            for item in results:
                href = item['href']
                text = item['text']

                # FILTRE VILLE: vérifier que l'annonce correspond à la ville configurée
                href_lower = href.lower()
                text_lower = text.lower()
                city_val = CONFIG.get('city', 'Toulouse').lower()
                allowed_postals = CONFIG.get('postal_codes', ['31000'])

                is_right_city = (
                    city_val in href_lower or
                    any(p in href_lower for p in allowed_postals) or
                    city_val in text_lower or
                    any(p in text_lower for p in allowed_postals)
                )
                # Exclure les autres villes (sauf la ville configurée)
                other_cities = ['toulouse', 'lyon', 'marseille', 'bordeaux', 'lille', 'nantes', 'montpellier', 'nice', 'strasbourg', 'paris']
                other_cities = [c for c in other_cities if c != city_val]
                is_other_city = any(c in href_lower for c in other_cities)

                if not is_right_city or is_other_city:
                    continue

                # Colocation detection
                is_coloc = 'coloc' in text_lower or 'co-loc' in text_lower or 'colocation' in text_lower
                if CONFIG.get('only_coloc', False):
                    if not is_coloc:
                        continue
                else:
                    if is_coloc and not CONFIG.get('allow_coloc', False):
                        continue

                full_url = f"https://www.pap.fr{href}" if href.startswith('/') else href
                price = parse_price(text)
                surface = parse_surface(text)
                pieces_n = parse_pieces(text)

                if price and price <= CONFIG.get('max_price', 600):
                    # Extraire le quartier du texte
                    city_display = CONFIG.get('city', 'Toulouse')
                    quartier = city_display
                    # Pour Toulouse : chercher les quartiers du centre
                    if city_display.lower() == 'toulouse':
                        for q in CENTRE_QUARTIERS:
                            if q in text_lower:
                                quartier = q.title()
                                break
                    # Pour Paris : chercher le code postal dans le texte
                    elif city_display.lower() == 'paris':
                        for p in allowed_postals:
                            if p in text_lower or p in href_lower:
                                arr_num = int(p[3:])  # 75011 → 11
                                quartier = f"Paris {arr_num}e"
                                break

                    annonces.append(Annonce(
                        titre=item['linkText'][:60].strip() or text[:60].strip(),
                        url=full_url,
                        loyer_cc=price,
                        surface=surface,
                        pieces=pieces_n,
                        quartier=quartier,
                        source="PAP"
                    ))

            # On continue sur tous les codes postaux (pas de break)
            # sauf pour le fallback URL générale qui vient en dernier
            if annonces and search_url.endswith(f'{city_slug}-{price_seg}'):
                break  # Fallback général atteint et résultats trouvés

        except Exception as e:
            print(f"  ⚠ Erreur PAP: {e}")

    page.close()
    print(f"  ✓ PAP: {len(annonces)} annonces trouvées")
    return annonces


# ─── Scraper SeLoger ──────────────────────────────────────────────────

def scrape_seloger(context: BrowserContext) -> list[Annonce]:
    """Scrape SeLoger via Playwright."""
    print("\n🔍 Scraping SeLoger...")
    annonces = []
    api_data = []

    page = context.new_page()

    def intercept(response: Response):
        url = response.url
        if any(k in url for k in ['search', 'listing', 'results']):
            try:
                ct = response.headers.get('content-type', '')
                if 'json' in ct:
                    body = response.json()
                    api_data.append(body)
                    print(f"  [API] {url[:60]}...")
            except Exception:
                pass

    page.on('response', intercept)

    try:
        max_price = CONFIG.get('max_price', 600)
        page.goto(
            f'https://www.seloger.com/list.htm?projects=1&types=1,2&places=[{{ci:310555}}]&price=NaN/{max_price}&enterprise=0&qsVersion=1.0',
            wait_until='domcontentloaded', timeout=30000
        )
        page.wait_for_timeout(6000)

        # Cookies
        try:
            btn = page.query_selector('#didomi-notice-agree-button, button[id*="accept"]')
            if btn:
                btn.click()
                page.wait_for_timeout(2000)
        except Exception:
            pass

        for _ in range(3):
            page.evaluate('window.scrollBy(0, 600)')
            page.wait_for_timeout(1000)

        # Parser API
        for data in api_data:
            items = None
            for key in ['cards', 'items', 'results', 'realEstateAds', 'classifieds']:
                if key in data and isinstance(data[key], list):
                    items = data[key]
                    break
            if items is None and isinstance(data, list):
                items = data
            if not items:
                continue

            for ad in items:
                if not isinstance(ad, dict):
                    continue
                try:
                    ad_id = ad.get('id', ad.get('classifiedId', ''))
                    titre = ad.get('title', ad.get('description', ''))[:80]

                    # Prix — structure variable
                    price = ad.get('price')
                    if isinstance(price, dict):
                        price = price.get('value', price.get('price'))
                    pricing = ad.get('pricing', {})
                    if isinstance(pricing, dict) and not price:
                        price = pricing.get('price', pricing.get('value'))

                    if not price or float(price) > CONFIG.get('max_price', 600):
                        continue

                    surface = ad.get('livingArea', ad.get('surface', ad.get('surfaceArea')))
                    rooms = ad.get('rooms', ad.get('roomsQuantity'))
                    city = ad.get('city', ad.get('cityLabel', ''))
                    district = ad.get('district', ad.get('districtLabel', ''))
                    zipcode = ad.get('zipCode', ad.get('zipcode', ''))

                    permalink = ad.get('permalink', ad.get('classifiedURL', ''))
                    if permalink:
                        ad_url = permalink if permalink.startswith('http') else f"https://www.seloger.com{permalink}"
                    elif ad_id:
                        ad_url = f"https://www.seloger.com/annonces/locations/appartement/toulouse-31/{ad_id}.htm"
                    else:
                        continue

                    quartier = district if isinstance(district, str) else f"{city} ({zipcode})"

                    annonces.append(Annonce(
                        titre=titre or "Appartement",
                        url=ad_url,
                        loyer_cc=float(price),
                        surface=float(surface) if surface else None,
                        pieces=int(rooms) if rooms else None,
                        quartier=quartier,
                        source="SeLoger"
                    ))
                except Exception:
                    pass

        # Fallback HTML
        if not annonces:
            print("  → Tentative HTML...")
            results = page.evaluate('''() => {
                const items = [];
                document.querySelectorAll('a[href*="annonce"], a[href*=".htm"], [class*="Card"] a').forEach(a => {
                    const href = a.href || '';
                    const text = (a.closest('article, [class*="Card"]') || a).innerText || '';
                    if (href && text.length > 20) {
                        items.push({href, text: text.substring(0, 300)});
                    }
                });
                return items;
            }''')
            print(f"  → {len(results)} liens HTML")

    except Exception as e:
        print(f"  ⚠ Erreur SeLoger: {e}")

    page.close()
    print(f"  ✓ SeLoger: {len(annonces)} annonces trouvées")
    return annonces


# ─── Scraper via recherche directe API Leboncoin ──────────────────────

def scrape_lbc_api_direct(context: BrowserContext) -> list[Annonce]:
    """Tente un appel direct à l'API Leboncoin depuis le contexte navigateur."""
    print("\n🔍 Leboncoin API directe...")
    annonces = []
    page = context.new_page()

    try:
        # D'abord charger la page pour avoir les cookies
        page.goto('https://www.leboncoin.fr', wait_until='domcontentloaded', timeout=15000)
        page.wait_for_timeout(3000)

        # Accepter cookies
        try:
            btn = page.query_selector('#didomi-notice-agree-button')
            if btn:
                btn.click()
                page.wait_for_timeout(2000)
        except Exception:
            pass

        # Maintenant faire un appel API depuis le contexte du navigateur
        _city = CONFIG.get('city', 'Toulouse')
        _postals = CONFIG.get('postal_codes', ['31000']) or ['31000']
        # dept/region selon la ville
        _is_paris = _city.lower() == 'paris'
        _dept = '75' if _is_paris else '31'
        _region = '12' if _is_paris else '16'
        _lbc_locations = [
            {"city": _city, "zipcode": p, "department_id": _dept, "region_id": _region}
            for p in _postals
        ]
        params = {
            'maxPrice': CONFIG.get('max_price', 600),
            'locations': _lbc_locations,
        }

        api_result = page.evaluate(
            '''async (params) => {
                const {maxPrice, locations} = params;
                try {
                    const response = await fetch('https://api.leboncoin.fr/finder/search', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Accept': 'application/json',
                            'Origin': 'https://www.leboncoin.fr',
                            'Referer': 'https://www.leboncoin.fr/',
                        },
                        body: JSON.stringify({
                            "limit": 50,
                            "limit_alu": 3,
                            "filters": {
                                "category": {"id": "10"},
                                "enums": {
                                    "real_estate_type": ["2"],
                                    "ad_type": ["offer"]
                                },
                                "location": {
                                    "locations": locations
                                },
                                "ranges": {
                                    "price": {"max": maxPrice}
                                }
                            },
                            "sort_by": "time",
                            "sort_order": "desc"
                        })
                    });
                    if (response.ok) {
                        return await response.json();
                    } else {
                        return {error: response.status, statusText: response.statusText};
                    }
                } catch(e) {
                    return {error: e.message};
                }
            }''',
            params
        )

        if isinstance(api_result, dict) and 'error' not in api_result:
            ads = api_result.get('ads', [])
            print(f"  → API OK: {len(ads)} annonces")
            for ad in ads:
                a = parse_lbc_ad(ad)
                if a:
                    annonces.append(a)
        else:
            print(f"  → API échouée: {api_result}")

    except Exception as e:
        print(f"  ⚠ Erreur API LBC: {e}")

    page.close()
    print(f"  ✓ Leboncoin API: {len(annonces)} annonces")
    return annonces


# ─── Agrégation et filtrage ───────────────────────────────────────────

def deduplicate(annonces: list[Annonce]) -> list[Annonce]:
    seen_urls = set()
    seen_keys = set()
    unique = []
    for a in annonces:
        # Dédoublonnage par URL
        url_key = a.url.rstrip('/')
        if url_key in seen_urls:
            continue
        seen_urls.add(url_key)

        # Dédoublonnage par caractéristiques
        dk = a.dedup_key
        if dk in seen_keys:
            continue
        seen_keys.add(dk)

        unique.append(a)
    return unique


def _parse_available_from(val: str):
    """Retourne un objet date depuis une chaîne YYYY-MM-DD, ou None si invalide."""
    if not val:
        return None
    try:
        from datetime import date as _date
        parts = val.split('-')
        if len(parts) == 1:
            # format "2026" seul — ignorer
            return None
        if len(parts) == 2:
            # format "2026-09" → premier du mois
            return _date(int(parts[0]), int(parts[1]), 1)
        return _date.fromisoformat(val)
    except (ValueError, TypeError):
        return None


def filter_annonces(annonces: list[Annonce]) -> list[Annonce]:
    from datetime import date as _date
    filtered = []
    avail_limit = _parse_available_from(CONFIG.get('available_from', ''))
    for a in annonces:
        if not a.loyer_cc or a.loyer_cc > CONFIG.get('max_price', 600):
            continue
        if not a.url or not a.url.startswith('http'):
            continue

        # Filtrage localisation
        postal = ''
        pc_match = re.search(r'\d{5}', a.quartier or '')
        if pc_match:
            postal = pc_match.group(0)

        if CONFIG.get('center_only', True) and not is_valid_location(a.quartier or '', postal):
            continue

        # Filtrage par date de disponibilité
        if avail_limit:
            dispo = (a.disponibilite or '').strip()
            if dispo and dispo not in ('Non précisé', 'Immédiat', ''):
                try:
                    ad_date = _date.fromisoformat(dispo)
                    if ad_date < avail_limit:
                        continue  # disponible trop tôt
                except ValueError:
                    pass  # date non parseable → on garde l'annonce
            # "Immédiat" ou "Non précisé" → on garde (logement potentiellement libre)

        filtered.append(a)
    return filtered


def _city_slug(city: str) -> str:
    return (city or 'recherche').strip().lower().replace(' ', '-')


def format_results(annonces: list[Annonce]):
    if not annonces:
        print("\n❌ Aucune annonce trouvée après filtrage.")
        return

    print(f"\n{'='*80}")
    print(f"  RÉSULTATS FINAUX: {len(annonces)} annonces valides")
    print(f"{'='*80}")

    by_price = sorted(annonces, key=lambda a: a.loyer_cc or 9999)

    # Aperçu CLI (concis)
    print(f"\n{'#':>3}  {'CC':>6}  {'m²':>5}  {'P.':>3}  {'Quartier':<30}  Source")
    for i, a in enumerate(by_price, 1):
        sf = f"{a.surface:.0f}" if a.surface else "?"
        pcs = str(a.pieces) if a.pieces else "?"
        cc = f"{a.loyer_cc:.0f}€" if a.loyer_cc else "?"
        q = (a.quartier or '')[:30]
        print(f"{i:>3}  {cc:>6}  {sf:>5}  {pcs:>3}  {q:<30}  {a.source}")

    # Export dans results/<ville>/
    city = CONFIG.get('city', 'Toulouse')
    out_dir = os.path.join('results', _city_slug(city))
    os.makedirs(out_dir, exist_ok=True)

    export = [asdict(a) for a in by_price]
    json_path = os.path.join(out_dir, 'annonces.json')
    csv_path = os.path.join(out_dir, 'annonces.csv')

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(export, f, ensure_ascii=False, indent=2)

    df = pd.DataFrame(export)
    df.to_csv(csv_path, index=False, encoding='utf-8')

    print(f"\n💾 Exporté : {json_path} + {csv_path}")

    # Génération du rapport markdown
    try:
        from generate_report import generate
        report_path = os.path.join(out_dir, 'rapport.md')
        generate(export, CONFIG, report_path)
    except Exception as e:
        print(f"⚠ Erreur génération rapport : {e}")


# ─── Main ─────────────────────────────────────────────────────────────

def main():
    city = CONFIG.get('city', 'Toulouse')
    center_text = "centre-ville " if CONFIG.get('center_only', True) else ""
    print(f"🏠 Scraper v2 — {city} {center_text}≤ {CONFIG.get('max_price', 600)}€ CC")
    print("=" * 60)

    all_annonces = []

    # headless=False contourne l'anti-bot de Bien'ici mais ouvre une fenêtre
    use_headless = CONFIG.get('headless', False)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=use_headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-infobars',
                '--window-size=1920,1080',
                '--ignore-certificate-errors',
                '--ignore-certificate-errors-spki-list',
                '--disable-features=CertificateTransparencyComponentUpdater',
            ],
            ignore_default_args=['--enable-automation'],
        )

        context = browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
            locale='fr-FR',
            timezone_id='Europe/Paris',
            ignore_https_errors=True,
        )

        # Masquer l'automatisation
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
        """)

        scrapers = [
            scrape_bienici,
            scrape_lbc_api_direct,
            scrape_leboncoin,
            scrape_pap,
            scrape_seloger,
        ]

        for fn in scrapers:
            try:
                results = fn(context)
                all_annonces.extend(results)
            except Exception as e:
                print(f"\n❌ Erreur fatale {fn.__name__}: {e}")

        browser.close()

    print(f"\n{'='*60}")
    print(f"📦 Total brut: {len(all_annonces)} annonces")

    unique = deduplicate(all_annonces)
    print(f"📦 Après dédoublonnage: {len(unique)}")

    final = filter_annonces(unique)
    print(f"📦 Après filtrage centre-ville: {len(final)}")

    format_results(final)
    return final


if __name__ == '__main__':
    results = main()
    sys.exit(0 if results else 1)

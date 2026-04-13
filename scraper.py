#!/usr/bin/env python3
"""
Scraper d'annonces immobilières - Toulouse centre-ville ≤ 600€ CC
v2 — Corrections: parsing Bien'ici, filtrage PAP, pagination, exclusion colocations
"""

import json
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


# ─── Scraper Bien'ici (API interception avec pagination) ──────────────

def scrape_bienici(context: BrowserContext) -> list[Annonce]:
    """Scrape Bien'ici via interception des appels API avec pagination."""
    print("\n🔍 Scraping Bien'ici...")
    annonces = []
    all_ads_raw = []
    total_count = 0

    # Paginer pour couvrir un maximum d'annonces
    for page_num in range(1, 12):  # Pages 1 à 11
        api_responses = []
        page = context.new_page()

        def make_interceptor(responses_list):
            def intercept(response: Response):
                if 'realEstateAds.json' in response.url:
                    try:
                        ct = response.headers.get('content-type', '')
                        if 'json' in ct:
                            body = response.json()
                            responses_list.append(body)
                    except Exception:
                        pass
            return intercept

        page.on('response', make_interceptor(api_responses))

        url = f'https://www.bienici.com/recherche/location/toulouse-31000?prix-max=600&type=appartement&page={page_num}'
        try:
            print(f"  → Page {page_num}...")
            page.goto(url, wait_until='domcontentloaded', timeout=30000)
            page.wait_for_timeout(4000)

            # Scroll
            for _ in range(3):
                page.evaluate('window.scrollBy(0, 600)')
                page.wait_for_timeout(500)

            page.wait_for_timeout(2000)

        except Exception as e:
            print(f"  ⚠ Erreur navigation page {page_num}: {e}")

        page.close()

        for data in api_responses:
            if isinstance(data, dict):
                if total_count == 0:
                    total_count = data.get('total', 0)
                    print(f"  → Total annonces sur Bien'ici: {total_count}")
                ads = data.get('realEstateAds', [])
                all_ads_raw.extend(ads)

        # Si on a tout récupéré, arrêter
        if len(all_ads_raw) >= total_count or not api_responses:
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

            # Exclure colocations
            if ad.get('flatSharing', False):
                continue

            titre = ad.get('title', '') or ''
            if 'coloc' in titre.lower() or 'co-loc' in titre.lower():
                continue

            price = ad.get('price')
            if not price or float(price) > 600:
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
            # Format Bien'ici: /annonce/location/toulouse-31000/ID
            ad_url = f"https://www.bienici.com/annonce/location/toulouse-{postal_code}/{ad_id}"

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

    print(f"  ✓ Bien'ici: {len(annonces)} annonces (après exclusion colocations et >600€)")
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

    search_url = 'https://www.leboncoin.fr/recherche?category=10&locations=Toulouse_31000&real_estate_type=2&price=max-600'

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

        # Debug: sauvegarder le HTML
        try:
            html = page.content()
            with open('debug_leboncoin.html', 'w') as f:
                f.write(html)
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

    if not price or price > 600:
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

    # Tenter plusieurs formats d'URL PAP pour Toulouse
    urls_to_try = [
        'https://www.pap.fr/annonce/location-appartement-toulouse-31000-g439-jusqu-a-600-euros',
        'https://www.pap.fr/annonce/locations-appartement-toulouse-31000-jusqu-a-600-euros',
    ]

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

                # FILTRE STRICT: le lien ou le texte doit contenir "toulouse" ou "31000"
                href_lower = href.lower()
                text_lower = text.lower()

                is_toulouse = (
                    'toulouse' in href_lower or
                    '31000' in href_lower or
                    'toulouse' in text_lower or
                    '31000' in text_lower
                )

                # Exclure explicitement Paris et autres villes
                is_other_city = any(city in href_lower for city in ['paris', 'lyon', 'marseille', 'bordeaux', 'lille', 'nantes', 'montpellier', 'nice', 'strasbourg'])

                if not is_toulouse or is_other_city:
                    continue

                # Exclure colocations
                if 'coloc' in text_lower or 'co-loc' in text_lower:
                    continue

                full_url = f"https://www.pap.fr{href}" if href.startswith('/') else href
                price = parse_price(text)
                surface = parse_surface(text)
                pieces_n = parse_pieces(text)

                if price and price <= 600:
                    # Extraire le quartier du texte
                    quartier = "Toulouse"
                    for q in CENTRE_QUARTIERS:
                        if q in text_lower:
                            quartier = q.title()
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

            if annonces:
                break  # Pas besoin d'essayer l'autre URL

        except Exception as e:
            print(f"  ⚠ Erreur PAP: {e}")

    # Debug
    try:
        html = page.content()
        with open('debug_pap.html', 'w') as f:
            f.write(html)
    except Exception:
        pass

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
        page.goto(
            'https://www.seloger.com/list.htm?projects=1&types=1,2&places=[{ci:310555}]&price=NaN/600&enterprise=0&qsVersion=1.0',
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

                    if not price or float(price) > 600:
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

    # Debug
    try:
        with open('debug_seloger.html', 'w') as f:
            f.write(page.content())
    except Exception:
        pass

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
        api_result = page.evaluate('''async () => {
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
                                "locations": [{"city": "Toulouse", "zipcode": "31000", "department_id": "31", "region_id": "16"}]
                            },
                            "ranges": {
                                "price": {"max": 600}
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
        }''')

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


def filter_annonces(annonces: list[Annonce]) -> list[Annonce]:
    filtered = []
    for a in annonces:
        if not a.loyer_cc or a.loyer_cc > 600:
            continue
        if not a.url or not a.url.startswith('http'):
            continue

        # Centre-ville check
        postal = ''
        pc_match = re.search(r'3\d{4}', a.quartier)
        if pc_match:
            postal = pc_match.group(0)

        if not is_centre_ville(a.quartier, postal):
            continue

        filtered.append(a)
    return filtered


def format_results(annonces: list[Annonce]):
    if not annonces:
        print("\n❌ Aucune annonce trouvée après filtrage.")
        return

    print(f"\n{'='*100}")
    print(f"  RÉSULTATS FINAUX: {len(annonces)} annonces valides")
    print(f"{'='*100}")

    # Liste 1 — Par prix croissant
    by_price = sorted(annonces, key=lambda a: a.loyer_cc or 9999)
    print("\n📊 LISTE 1 — Par prix croissant\n")
    header = f"| {'#':>3} | {'Titre':<40} | {'Quartier':<25} | {'CC':>6} | {'HC':>6} | {'m²':>5} | {'P.':>3} | {'Dispo':<12} | {'Source':<10} |"
    sep = f"|{'-'*5}|{'-'*42}|{'-'*27}|{'-'*8}|{'-'*8}|{'-'*7}|{'-'*5}|{'-'*14}|{'-'*12}|"
    print(header)
    print(sep)
    for i, a in enumerate(by_price, 1):
        sf = f"{a.surface:.0f}" if a.surface else "?"
        pcs = str(a.pieces) if a.pieces else "?"
        cc = f"{a.loyer_cc:.0f}€" if a.loyer_cc else "?"
        hc = f"{a.loyer_hc:.0f}€" if a.loyer_hc else "-"
        titre_short = a.titre[:40] if a.titre else ""
        quartier_short = a.quartier[:25] if a.quartier else ""
        dispo_short = a.disponibilite[:12]
        print(f"| {i:>3} | {titre_short:<40} | {quartier_short:<25} | {cc:>6} | {hc:>6} | {sf:>5} | {pcs:>3} | {dispo_short:<12} | {a.source:<10} |")

    print("\n\nURLs des annonces (Liste 1 — prix croissant) :")
    for i, a in enumerate(by_price, 1):
        print(f"  {i:>2}. {a.url}")

    # Liste 2 — Par surface décroissante
    by_surface = sorted(annonces, key=lambda a: a.surface or 0, reverse=True)
    print("\n\n📊 LISTE 2 — Par surface décroissante\n")
    print(header)
    print(sep)
    for i, a in enumerate(by_surface, 1):
        sf = f"{a.surface:.0f}" if a.surface else "?"
        pcs = str(a.pieces) if a.pieces else "?"
        cc = f"{a.loyer_cc:.0f}€" if a.loyer_cc else "?"
        hc = f"{a.loyer_hc:.0f}€" if a.loyer_hc else "-"
        titre_short = a.titre[:40] if a.titre else ""
        quartier_short = a.quartier[:25] if a.quartier else ""
        dispo_short = a.disponibilite[:12]
        print(f"| {i:>3} | {titre_short:<40} | {quartier_short:<25} | {cc:>6} | {hc:>6} | {sf:>5} | {pcs:>3} | {dispo_short:<12} | {a.source:<10} |")

    print("\n\nURLs des annonces (Liste 2 — surface décroissante) :")
    for i, a in enumerate(by_surface, 1):
        print(f"  {i:>2}. {a.url}")

    # Export
    export = [asdict(a) for a in by_price]
    with open('resultats_annonces.json', 'w', encoding='utf-8') as f:
        json.dump(export, f, ensure_ascii=False, indent=2)

    df = pd.DataFrame(export)
    df.to_csv('resultats_annonces.csv', index=False, encoding='utf-8')

    print(f"\n💾 Exporté: resultats_annonces.json + resultats_annonces.csv")


# ─── Main ─────────────────────────────────────────────────────────────

def main():
    print("🏠 Scraper v2 — Toulouse centre-ville ≤ 600€ CC")
    print("=" * 60)

    all_annonces = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
            ]
        )

        context = browser.new_context(
            user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
            locale='fr-FR',
            timezone_id='Europe/Paris',
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

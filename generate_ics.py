#!/usr/bin/env python3
"""
Génère un fichier concerts-metal.ics à partir de flux RSS de concerts-metal.com.
Utilise cloudscraper pour contourner la protection anti-bot du site.
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import cloudscraper

# === CONFIGURATION ===
RSS_URLS = [
    "https://www.concerts-metal.com/rss/FR_Paris.xml",
    "https://www.concerts-metal.com/rss/FR_Montreuil.xml",
]
OUTPUT_FILE = "concerts-metal.ics"
# =====================


def create_scraper():
    """Crée un scraper qui imite un navigateur Chrome sur Windows."""
    return cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'desktop': True,
        }
    )


def escape_ics(text):
    """Échappe les caractères spéciaux selon la RFC 5545."""
    if not text:
        return ""
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    text = text.replace("\n", "\\n")
    return text


def parse_event(item, seen_uids):
    """Transforme un <item> RSS en dict d'événement, ou None si non exploitable."""
    title_elem = item.find('title')
    link_elem = item.find('link')
    desc_elem = item.find('description')

    if title_elem is None or title_elem.text is None:
        return None

    title_text = title_elem.text.strip()
    link = link_elem.text.strip() if link_elem is not None and link_elem.text else ""

    # Retirer le paramètre de tracking ?rss=... ajouté par le flux
    # Regex permissive : supprime ?rss=... n'importe où dans l'URL
    link = re.sub(r'\?rss=[^&\s]*', '', link).strip()

    # Date extraite du <title> (le <pubDate> est la date de publication, pas du concert)
    match = re.search(r'(\d{4}-\d{2}-\d{2})', title_text)
    if not match:
        return None
    date_str = match.group(1)
    try:
        event_date = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None

    # Déduplication par lien (ou date+titre si pas de lien)
    uid = link if link else f"{date_str}-{title_text}"
    if uid in seen_uids:
        return None
    seen_uids.add(uid)

    # Nettoyage du titre
    clean_title = re.sub(r'^\d{4}-\d{2}-\d{2}\s*-\s*', '', title_text)
    clean_title = re.sub(r'\s*@\s*[^@]+$', '', clean_title).strip()
    if not clean_title:
        clean_title = "Concert metal"

    # Image (optionnel)
    image_url = ""
    if desc_elem is not None and desc_elem.text:
        img_match = re.search(r'<img[^>]+src="([^"]+)"', desc_elem.text)
        if img_match:
            image_url = img_match.group(1)

    return {
        "uid": uid,
        "date": event_date,
        "title": clean_title,
        "link": link,
        "image": image_url,
    }


def event_to_ics(event):
    """Convertit un dict d'événement en lignes iCalendar."""
    dtstamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    start = event["date"].strftime("%Y%m%d")
    end = (event["date"] + timedelta(days=1)).strftime("%Y%m%d")

    lines = [
        "BEGIN:VEVENT",
        f"UID:{event['uid']}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART;VALUE=DATE:{start}",
        f"DTEND;VALUE=DATE:{end}",
        f"SUMMARY:{escape_ics(event['title'])}",
    ]
    if event["link"]:
        lines.append(f"URL:{event['link']}")
        lines.append(f"DESCRIPTION:{escape_ics('Source: ' + event['link'])}")
    if event["image"]:
        lines.append(f"ATTACH;FMTTYPE=image/jpeg:{event['image']}")
    lines.append("END:VEVENT")
    return lines


def main():
    scraper = create_scraper()
    seen_uids = set()
    events = []  # on collecte d'abord tous les événements

    for rss_url in RSS_URLS:
        print(f"Téléchargement de {rss_url} ...")
        try:
            response = scraper.get(rss_url, timeout=30)
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except Exception as e:
            print(f"  ⚠️ Échec pour {rss_url} : {e}")
            continue

        channel = root.find('channel')
        if channel is None:
            print(f"  ⚠️ Pas de <channel> dans {rss_url}")
            continue

        for item in channel.findall('item'):
            event = parse_event(item, seen_uids)
            if event is not None:
                events.append(event)

    # Tri par date croissante
    events.sort(key=lambda e: e["date"])

    ics_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Metal Concerts//RSS to ICS//FR",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:Concerts Metal Paris & Montreuil",
    ]
    for event in events:
        ics_lines.extend(event_to_ics(event))
    ics_lines.append("END:VCALENDAR")

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write("\r\n".join(ics_lines))  # RFC 5545 : CRLF obligatoire

    print(f"✅ {OUTPUT_FILE} généré : {len(events)} événements.")


if __name__ == "__main__":
    main()
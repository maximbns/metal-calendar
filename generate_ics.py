#!/usr/bin/env python3
"""
Génère un fichier concerts-metal.ics à partir de la page broadcast de concerts-metal.com,
en filtrant sur Paris et Montreuil.
"""

import re
import time
import html as html_module
from datetime import datetime, timedelta, timezone

import cloudscraper

# === CONFIGURATION ===
BROADCAST_URL = "https://broadcast.concerts-metal.com/i-1500__fr.html"
OUTPUT_FILE = "concerts-metal.ics"
CITIES = {"paris", "montreuil"}
# =====================


def create_scraper():
    return cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
    )


def fetch_page(scraper, url, retries=3, wait=5):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            print(f"  Tentative {attempt}/{retries} ...")
            response = scraper.get(url, timeout=30)
            if response.status_code == 200:
                return response
            last_error = f"HTTP {response.status_code}"
            print(f"  ⚠️ Réponse {last_error}, on réessaie dans {wait}s.")
        except Exception as e:
            last_error = str(e)
            print(f"  ⚠️ Erreur : {e}, on réessaie dans {wait}s.")
        if attempt < retries:
            time.sleep(wait)
    print(f"  ❌ Échec définitif après {retries} tentatives : {last_error}")
    return None


def escape_ics(text):
    if not text:
        return ""
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    text = text.replace("\n", "\\n")
    return text


def parse_broadcast_page(html):
    events = []
    seen = set()
    total_links = 0
    total_format_ok = 0

    # Chaque concert : <a ... title="TITRE - VILLE - PAYS - DATE" ...>TITRE</a> @ SALLE,  VILLE<br/>
    pattern = re.compile(
        r'<a\s+[^>]*?title="([^"]+)"[^>]*>(.*?)</a>\s*@\s*([^<,]+),\s*([^<]+?)(?:<br|$)',
        re.DOTALL
    )

    for m in pattern.finditer(html):
        total_links += 1
        title_full = html_module.unescape(m.group(1))
        venue = html_module.unescape(m.group(3)).strip()

        # Format : "Titre - Ville - Pays - JJ/MM/AAAA"
        match = re.match(r'^(.*) - ([^-]+) - ([^-]+) - (\d{2}/\d{2}/\d{4})$', title_full)
        if not match:
            continue
        total_format_ok += 1

        title, city, country, date_str = match.groups()
        city = city.strip().lower()

        if city not in CITIES:
            continue

        day, month, year = date_str.split('/')
        try:
            event_date = datetime(int(year), int(month), int(day))
        except ValueError:
            continue

        uid = f"{event_date.strftime('%Y%m%d')}-{city}-{venue}-{title}"
        uid = re.sub(r'\s+', '-', uid.lower())
        if uid in seen:
            continue
        seen.add(uid)

        events.append({
            "uid": uid,
            "date": event_date,
            "title": title.strip(),
            "venue": venue,
            "city": city,
        })

    print(f"  {total_links} liens '@ Salle, Ville' analysés.")
    print(f"  {total_format_ok} avec format 'Titre - Ville - Pays - Date'.")
    return events


def event_to_ics(event):
    dtstamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    start = event["date"].strftime("%Y%m%d")
    end = (event["date"] + timedelta(days=1)).strftime("%Y%m%d")

    lines = [
        "BEGIN:VEVENT",
        f"UID:{event['uid']}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART;VALUE=DATE:{start}",
        f"DTEND;VALUE=DATE:{end}",
        f"SUMMARY:{escape_ics(event['title'])}",
        f"LOCATION:{escape_ics(event['venue'] + ', ' + event['city'].title())}",
        "END:VEVENT",
    ]
    return lines


def main():
    scraper = create_scraper()
    print(f"Téléchargement de {BROADCAST_URL} ...")
    response = fetch_page(scraper, BROADCAST_URL, retries=3, wait=5)
    if response is None:
        print("Impossible de récupérer la page. Le fichier .ics ne sera pas mis à jour.")
        return

    print(f"  Page récupérée ({len(response.text)} caractères).")
    events = parse_broadcast_page(response.text)
    print(f"  {len(events)} événements Paris/Montreuil trouvés.")

    events.sort(key=lambda e: e["date"])

    ics_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Metal Concerts//Broadcast to ICS//FR",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:Concerts Metal Paris & Montreuil",
    ]
    for event in events:
        ics_lines.extend(event_to_ics(event))
    ics_lines.append("END:VCALENDAR")

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write("\r\n".join(ics_lines))

    print(f"✅ {OUTPUT_FILE} généré : {len(events)} événements.")


if __name__ == "__main__":
    main()
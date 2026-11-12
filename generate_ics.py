#!/usr/bin/env python3
"""Génère un fichier ICS des concerts metal en Île-de-France
depuis broadcast.concerts-metal.com (page i-28).

v3 :
- Fuseau horaire explicite (Europe/Paris)
- Dédoublonnage (exact + quasi-doublons même jour/même salle)
- Nettoyage des titres (retrait de la salle/ville en double)
"""
import re
import sys
import time
import unicodedata
import html as html_module
from datetime import datetime, timedelta, timezone
import cloudscraper

BROADCAST_URL = "https://broadcast.concerts-metal.com/i-28__fr.html"
OUTPUT_FILE = "concerts-metal.ics"
TENTATIVE_VENUES = {"tba", "tbd", "tbc"}
ARTICLES = {"the", "a", "an", "le", "la", "les", "l", "un", "une"}


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
    print(f"  ❌ Échec après {retries} tentatives : {last_error}")
    return None


def escape_ics(text):
    if not text:
        return ""
    return (text.replace("\\", "\\\\")
                .replace(";", "\\;")
                .replace(",", "\\,")
                .replace("\n", "\\n"))


def strip_tags(s):
    return re.sub(r'<[^>]+>', '', s)


def _norm(s):
    """Normalise pour comparaison : minuscules, sans accents, sans article
    initial, ponctuation compactée en espaces."""
    if not s:
        return ""
    s = unicodedata.normalize('NFD', s)
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    for art in ("le ", "la ", "les ", "l ", "the ", "un ", "une "):
        if s.startswith(art):
            s = s[len(art):]
            break
    return s


def clean_title(title, venue, city):
    """Retire du titre les suffixes redondants avec la salle ou la ville.
    Ex. 'Roadrunners à la Marbrerie à Montreuil' → 'Roadrunners'."""
    if not title:
        return title

    venue_n = _norm(venue).replace(" ", "") if venue else ""
    city_n = _norm(city).replace(" ", "") if city else ""
    result = title.strip()

    # Séparateurs autorisés pour introduire un suffixe salle/ville,
    # du plus spécifique au plus général.
    separators = [
        r'\s*@\s*',
        r'\s+à\s+la\s+',
        r'\s+à\s+l\'',
        r'\s+à\s+l\s+',
        r'\s+au\s+',
        r'\s+aux\s+',
        r'\s+à\s+',
        r'\s+en\s+',
        r'\s+de\s+',
        r'\s+',
    ]

    # Itère pour retirer d'abord la ville puis la salle (ou l'inverse).
    for _ in range(5):
        stripped = False
        for sep_pat in separators:
            matches = list(re.finditer(sep_pat, result, re.IGNORECASE))
            if not matches:
                continue
            m = matches[-1]  # dernier séparateur de la chaîne
            tail = result[m.end():].strip()
            if not tail:
                continue
            tail_n = _norm(tail).replace(" ", "")
            if tail_n and tail_n in (venue_n, city_n):
                new = result[:m.start()].rstrip(' ,-–@')
                if new and new != result:
                    result = new
                    stripped = True
                    break
        if not stripped:
            break

    cleaned = result.strip(' ,-–@')
    return cleaned if len(cleaned) >= 2 else title


def parse_title_attr(title_attr):
    m = re.search(r'^(.*)\s+-\s+France\s+-\s+(\d{2}/\d{2}/\d{4})\s*$',
                  title_attr.strip(), re.DOTALL)
    if not m:
        return None, None, None
    rest = m.group(1)
    try:
        date = datetime.strptime(m.group(2), "%d/%m/%Y")
    except ValueError:
        return None, None, None
    title, city = (rest.rsplit(" - ", 1) if " - " in rest else (rest, ""))
    return title.strip(), city.strip(), date


def extract_attrs(tag):
    return dict(re.findall(r'([\w-]+)\s*=\s*"([^"]*)"', tag))


def headliner_key(title):
    """Premier mot significatif du titre, pour détecter les quasi-doublons."""
    words = _norm(title).split()
    if not words:
        return ""
    if words[0] in ARTICLES and len(words) > 1:
        return words[1]
    return words[0]


def find_duplicates(events):
    """Dédoublonne en deux passes :
      1) exact  : (date, titre normalisé, salle normalisée)
      2) quasi  : (date, salle normalisée, headliner) si headliner >= 3 lettres
    Retourne (kept_events, list_of_duplicate_groups)."""
    exact_seen = {}
    near_seen = {}
    kept = []
    duplicates = []

    for ev in events:
        key_exact = (ev['date'], _norm(ev['title']), _norm(ev['venue']))
        if key_exact in exact_seen:
            duplicates.append((exact_seen[key_exact], ev, "exact"))
            continue

        hk = headliner_key(ev['title'])
        key_near = (ev['date'], _norm(ev['venue']), hk)
        if hk and len(hk) >= 3 and key_near in near_seen:
            duplicates.append((near_seen[key_near], ev, "near"))
            continue

        exact_seen[key_exact] = ev
        if hk and len(hk) >= 3:
            near_seen[key_near] = ev
        kept.append(ev)

    return kept, duplicates


def parse_broadcast_page(html):
    events = []
    skipped = 0
    pattern = re.compile(
        r'<a\s+([^>]*?)>([^<]*?)</a>\s*@\s*([^,<]+?),\s*([^<]*)<br',
        re.DOTALL
    )

    for m in pattern.finditer(html):
        attrs = extract_attrs(m.group(1))
        title_attr = html_module.unescape(attrs.get('title', ''))
        url = attrs.get('href', '').strip()
        link_text = html_module.unescape(strip_tags(m.group(2))).strip()
        venue = html_module.unescape(strip_tags(m.group(3))).strip()
        city_inline = html_module.unescape(strip_tags(m.group(4))).strip()

        t_title, t_city, t_date = parse_title_attr(title_attr)
        if not t_date:
            skipped += 1
            continue

        raw_title = link_text or t_title
        city = t_city or city_inline
        if not raw_title or not city:
            skipped += 1
            continue

        title = clean_title(raw_title, venue, city)

        id_match = re.search(r'-(\d+)\.html', url)
        concert_id = id_match.group(1) if id_match else ''
        clean_url = re.sub(r'\?i=\d+$', '', url)

        uid_base = concert_id or re.sub(r'[^a-z0-9]', '', title.lower())[:25]
        uid = f"{t_date.strftime('%Y%m%d')}-{uid_base}"

        events.append({
            'uid': uid,
            'title': title,
            'venue': venue,
            'city': city,
            'date': t_date,
            'link': clean_url,
        })

    if skipped:
        print(f"  ℹ️ {skipped} entrée(s) ignorée(s) (parsing impossible).")
    return events


def event_to_ics(event):
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    start = event["date"].strftime("%Y%m%d")
    end = (event["date"] + timedelta(days=1)).strftime("%Y%m%d")

    venue = event['venue'].strip()
    location = f"{venue}, {event['city']}" if venue else event['city']

    is_tentative = venue.lower() in TENTATIVE_VENUES or not venue
    status = "TENTATIVE" if is_tentative else "CONFIRMED"

    lines = [
        "BEGIN:VEVENT",
        f"UID:{event['uid']}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART;VALUE=DATE:{start}",
        f"DTEND;VALUE=DATE:{end}",
        f"SUMMARY:{escape_ics(event['title'])}",
        f"LOCATION:{escape_ics(location)}",
        f"STATUS:{status}",
        "CATEGORIES:Concert,Metal",
    ]
    if event.get("link"):
        lines.append(f"URL:{event['link']}")
    lines.append("END:VEVENT")
    return lines


def main():
    scraper = create_scraper()
    print(f"Téléchargement de {BROADCAST_URL} ...")
    response = fetch_page(scraper, BROADCAST_URL, retries=3, wait=5)
    if response is None:
        print("❌ Impossible de récupérer la page.")
        sys.exit(1)

    print(f"  Page récupérée ({len(response.text)} caractères).")
    raw_events = parse_broadcast_page(response.text)
    print(f"  {len(raw_events)} événement(s) brut(s).")

    if not raw_events:
        print("⚠️ Aucun événement. Le fichier existant n'est pas écrasé.")
        return

    events, duplicates = find_duplicates(raw_events)
    if duplicates:
        print(f"  🔁 {len(duplicates)} doublon(s)/quasi-doublon(s) retiré(s) :")
        for a, b, kind in duplicates:
            print(f"     - [{kind}] {b['date'].strftime('%d/%m/%Y')} "
                  f"{b['title']!r} @ {b['venue']} "
                  f"(garde : {a['title']!r})")
    print(f"  {len(events)} événement(s) unique(s) conservé(s).")

    events.sort(key=lambda e: e["date"])

    n_confirmed = sum(1 for e in events
                      if e['venue'].lower() not in TENTATIVE_VENUES and e['venue'])
    n_tentative = len(events) - n_confirmed
    print(f"  → {n_confirmed} confirmé(s), {n_tentative} tentative(s).")

    ics_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Metal Concerts//Broadcast to ICS//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Concerts Metal Île-de-France",
        "X-WR-CALDESC:Concerts metal et assimilés en Île-de-France (source : broadcast.concerts-metal.com)",
        "X-WR-TIMEZONE:Europe/Paris",
        "REFRESH-INTERVAL;VALUE=DURATION:P1D",
        "X-PUBLISHED-TTL:P1D",
    ]
    for event in events:
        ics_lines.extend(event_to_ics(event))
    ics_lines.append("END:VCALENDAR")

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write("\r\n".join(ics_lines))

    print(f"✅ {OUTPUT_FILE} généré : {len(events)} événement(s).")


if __name__ == "__main__":
    main()

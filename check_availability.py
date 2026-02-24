"""
USOS Language Course Availability Monitor
Monitors language course groups for available spots, filters out schedule
conflicts using plan.ics, and sends Discord DM notifications on changes.

GitHub Secrets required:
  USOS_USERNAME      - USOS login (nr albumu)
  USOS_PASSWORD      - USOS password
  DISCORD_BOT_TOKEN  - Discord bot token
  DISCORD_USER_ID    - Your Discord user ID (for DMs)
"""

import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "beautifulsoup4"])
    import requests
    from bs4 import BeautifulSoup


# --- Configuration ---
BASE_URL = "https://usosweb.usos.pw.edu.pl/kontroler.php"
CAS_URL = "https://cas.usos.pw.edu.pl/cas/login"

# =============================================================================
# KTÓRE LEKTORATY MONITOROWAĆ?
# Odkomentuj lub zakomentuj rejestracje które chcesz obserwować.
# Możesz włączyć więcej niż jedną jednocześnie.
# =============================================================================
CDYD_KOD = "2026L"  # Semestr (nie zmieniaj)

REGISTRATIONS = [
    # Języki od podstaw — dla studentów 1. roku (M1), poziom A1
    {"rej_kod": "6420-1000-2026L-A1M1", "name": "Języki od podstaw (M1)"},

    # Inne języki A1 — dla studentów 2. i 3. roku (M2, M3), poziom A1
    # {"rej_kod": "6420-1000-2026L-A1", "name": "Inne języki A1 (M2, M3)"},

    # Języki A2–B2 — kontynuacja, poziomy A2, B1, B2
    # {"rej_kod": "6420-1000-2026L-A2B2", "name": "Języki A2–B2"},

    # Angielski tematyczny B2/B2+/C1
    # {"rej_kod": "6420-1000-2026L-LTA", "name": "Angielski tematyczny B2/B2+/C1"},

    # Angielski tematyczny C1+/C2
    # {"rej_kod": "6420-1000-2026L-LTC", "name": "Angielski tematyczny C1+/C2"},
]
# =============================================================================

SCHEDULE_FILE = Path(__file__).parent / "plan.ics"
STATE_FILE = Path(__file__).parent / "previous_state.json"

USERNAME = os.environ.get("USOS_USERNAME", "")
PASSWORD = os.environ.get("USOS_PASSWORD", "")
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_USER_ID = os.environ.get("DISCORD_USER_ID", "")

# Weekday index -> Polish day name (matching USOS scraper output)
DAYS_PL = {
    0: "Poniedziałek",
    1: "Wtorek",
    2: "Środa",
    3: "Czwartek",
    4: "Piątek",
    5: "Sobota",
    6: "Niedziela",
}

# A slot must appear at least this many times to be treated as a regular class
# (filters out one-off substitutions, semester-edge anomalies, etc.)
MIN_OCCURRENCES = 3


# --- ICS Schedule Parser ---
def _unfold_ics(text):
    """Unfold ICS continuation lines (RFC 5545: lines starting with space/tab)."""
    result = []
    for line in text.splitlines():
        if line.startswith((" ", "\t")) and result:
            result[-1] += line[1:]
        else:
            result.append(line)
    return "\n".join(result)


def _time_to_minutes(t):
    """Convert HH:MM string to minutes since midnight."""
    h, m = t.strip().split(":")
    return int(h) * 60 + int(m)


def load_schedule_from_ics(ics_path):
    """
    Parse a USOS .ics export and extract the regular weekly schedule.

    Counts how many times each (weekday, start, end) slot appears across the
    semester. Slots with >= MIN_OCCURRENCES are kept as regular classes.

    Returns list of (day_pl, start_min, end_min) tuples.
    """
    with open(ics_path, encoding="utf-8") as f:
        text = f.read()

    text = _unfold_ics(text)
    slot_counts = Counter()

    for block in re.split(r"BEGIN:VEVENT", text)[1:]:
        block = block.split("END:VEVENT")[0]
        props = {}
        for line in block.splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                props[key.split(";")[0].strip()] = val.strip()

        dtstart = props.get("DTSTART", "")
        dtend = props.get("DTEND", "")
        if not dtstart or not dtend:
            continue
        try:
            ds = datetime.strptime(dtstart[:15], "%Y%m%dT%H%M%S")
            de = datetime.strptime(dtend[:15], "%Y%m%dT%H%M%S")
        except ValueError:
            continue

        slot_counts[(ds.weekday(), ds.strftime("%H:%M"), de.strftime("%H:%M"))] += 1

    schedule = []
    for (weekday, start_str, end_str), count in slot_counts.items():
        if count >= MIN_OCCURRENCES:
            schedule.append((DAYS_PL[weekday], _time_to_minutes(start_str), _time_to_minutes(end_str)))

    print(f"  Wczytano {len(schedule)} regularnych slotow z {ics_path.name} "
          f"(prog: >={MIN_OCCURRENCES} wystapien)")
    return schedule


# --- Schedule conflict detection ---
def has_conflict(group, schedule):
    """Return True if the group's time slot overlaps any slot in schedule."""
    try:
        g_start = _time_to_minutes(group["godz_start"])
        g_end = _time_to_minutes(group["godz_end"])
    except (ValueError, KeyError):
        return False
    g_dzien = group.get("dzien", "").strip()
    for s_dzien, s_start, s_end in schedule:
        if g_dzien == s_dzien and g_start < s_end and g_end > s_start:
            return True
    return False


# --- Discord Bot DM ---
DISCORD_API = "https://discord.com/api/v10"


def discord_dm(embed):
    """Send an embed DM to the configured user via Discord bot."""
    if not DISCORD_BOT_TOKEN or not DISCORD_USER_ID:
        print("  [SKIP] Brak DISCORD_BOT_TOKEN lub DISCORD_USER_ID")
        return

    headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        ch = requests.post(
            f"{DISCORD_API}/users/@me/channels",
            json={"recipient_id": DISCORD_USER_ID},
            headers=headers, timeout=10,
        )
        if ch.status_code != 200:
            print(f"  Discord: blad DM channel: {ch.status_code}")
            return
        channel_id = ch.json()["id"]
    except Exception as e:
        print(f"  Discord: wyjatek - {e}")
        return

    try:
        msg = requests.post(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            json={"embeds": [embed]},
            headers=headers, timeout=10,
        )
        print("  Discord DM: wyslano" if msg.status_code in (200, 201)
              else f"  Discord DM: blad {msg.status_code}")
    except Exception as e:
        print(f"  Discord: wyjatek - {e}")


def send_notification(title, description, color=0x00FF00, fields=None):
    embed = {
        "title": title,
        "description": description,
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "USOS Monitor"},
    }
    if fields:
        embed["fields"] = fields[:25]
    discord_dm(embed)


# --- CAS Login ---
def cas_login(session):
    from urllib.parse import urlparse
    print("Logowanie do USOS via CAS...")

    resp = session.get(f"{BASE_URL}?_action=logowanie", allow_redirects=True)
    cas_page_url = resp.url

    if "cas.usos.pw.edu.pl" not in cas_page_url:
        soup = BeautifulSoup(resp.text, "html.parser")
        cas_link = None
        for a in soup.find_all("a", href=True):
            if "cas" in a["href"].lower():
                cas_link = a["href"]
                break
        meta = soup.find("meta", attrs={"http-equiv": "refresh"})
        if meta:
            content = meta.get("content", "")
            if "url=" in content.lower():
                cas_link = content.split("url=", 1)[-1].strip("'\" ")
        if not cas_link:
            service_url = f"{BASE_URL}?_action=logowaniecas/index"
            cas_link = f"{CAS_URL}?service={requests.utils.quote(service_url, safe='')}"
        resp = session.get(cas_link, allow_redirects=True)
        cas_page_url = resp.url

    soup = BeautifulSoup(resp.text, "html.parser")
    form = soup.find("form")
    if not form:
        print("ERROR: Could not find login form")
        return False

    execution = ""
    for inp in form.find_all("input", {"type": "hidden"}):
        if inp.get("name") == "execution":
            execution = inp.get("value", "")

    form_action = form.get("action", "")
    if form_action.startswith("/"):
        parsed = urlparse(cas_page_url)
        form_action = f"{parsed.scheme}://{parsed.netloc}{form_action}"
    elif not form_action.startswith("http"):
        form_action = cas_page_url
    parsed_cas = urlparse(cas_page_url)
    if parsed_cas.query and "?" not in form_action:
        form_action = f"{form_action}?{parsed_cas.query}"

    resp = session.post(form_action, data={
        "username": USERNAME, "password": PASSWORD,
        "execution": execution, "_eventId": "submit", "geolocation": "",
    }, allow_redirects=True)
    resp.encoding = "utf-8"

    if "wyloguj" in resp.text.lower() or "zalogowany" in resp.text.lower():
        print("  OK - zalogowano!")
        return True
    check = session.get(f"{BASE_URL}?_action=dla_stud/rejestracja/kalendarz")
    if "wyloguj" in check.text.lower() or "kalendarz rejestracji" in check.text.lower():
        print("  OK - zalogowano!")
        return True
    print("  BLAD: Nie udalo sie zalogowac")
    return False


# --- USOS Scraping ---
def get_subjects(session, rej_kod):
    url = f"{BASE_URL}?_action=dla_stud/rejestracja/brdg2/wyborPrzedmiotu&rej_kod={rej_kod}"
    soup = BeautifulSoup(session.get(url).text, "html.parser")
    subjects = []
    for row in soup.select("tr[id]"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        link = cells[0].find("a")
        if link:
            subjects.append({"name": link.get_text(strip=True), "code": row.get("id", "")})
    print(f"  Znaleziono {len(subjects)} przedmiotow")
    return subjects


def get_groups(session, subject, rej_kod, cdyd_kod):
    url = (
        f"{BASE_URL}?_action=dla_stud/rejestracja/brdg2/grupyPrzedmiotu"
        f"&rej_kod={rej_kod}&prz_kod={subject['code']}"
        f"&cdyd_kod={cdyd_kod}&odczyt=1&showLocationColumn=on&formFlag=1"
    )
    soup = BeautifulSoup(session.get(url).text, "html.parser")
    groups = []

    table = soup.find("table", class_="grey")
    if not table:
        for t in soup.find_all("table"):
            if t.find(string=re.compile("Prowadzący|prowadzący")):
                table = t
                break
    if not table:
        return groups

    header_row = table.find("tr", class_="headnote")
    if not header_row:
        thead = table.find("thead")
        if thead:
            header_row = thead.find("tr")

    col_map = {}
    if header_row:
        for i, h in enumerate(header_row.find_all(["th", "td"])):
            text = h.find(string=True, recursive=False)
            text = text.strip().lower() if text else h.get_text(strip=True).lower()
            if text == "grupa":             col_map["grupa"] = i
            elif "prowadz" in text:         col_map["prowadzacy"] = i
            elif text == "termin":          col_map["termin"] = i
            elif "miejsce" in text:         col_map["miejsce"] = i
            elif "opis" in text:            col_map["opis"] = i
            elif "zapisanych" in text:      col_map["zapisanych"] = i
            elif "limit" in text and "rn" in text: col_map["limit"] = i

    tbody = table.find("tbody")
    rows = tbody.find_all("tr") if tbody else table.find_all("tr")

    for row in rows:
        if row.find("th") or "headnote" in (row.get("class") or []):
            continue
        cells = row.find_all("td")
        if len(cells) < 4:
            continue

        def get_cell(key, default=""):
            idx = col_map.get(key)
            return cells[idx].get_text(separator=" ", strip=True) if idx is not None and idx < len(cells) else default

        termin = get_cell("termin")
        prowadzacy = get_cell("prowadzacy")
        if not termin and not prowadzacy:
            continue

        try:
            zapisanych = int(re.sub(r"[^\d]", "", get_cell("zapisanych", "0")) or "0")
        except ValueError:
            zapisanych = 0
        try:
            limit = int(re.sub(r"[^\d]", "", get_cell("limit", "0")) or "0")
        except ValueError:
            limit = 0

        for dzien, godz_start, godz_end in re.findall(
            r"(\w+)\s+(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", termin or ""
        ):
            groups.append({
                "przedmiot": subject["name"],
                "kod_przedmiotu": subject["code"],
                "grupa": get_cell("grupa"),
                "prowadzacy": prowadzacy,
                "dzien": dzien,
                "godz_start": godz_start,
                "godz_end": godz_end,
                "miejsce": get_cell("miejsce"),
                "opis": get_cell("opis"),
                "zapisanych": zapisanych,
                "limit": limit,
            })
    return groups


# --- State management ---
def group_key(g):
    return f"{g['kod_przedmiotu']}|gr{g['grupa']}|{g['dzien']}|{g['godz_start']}"


def load_previous_state(path):
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_state(path, state):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# --- Main ---
def main():
    if not USERNAME or not PASSWORD:
        print("BLAD: Ustaw zmienne USOS_USERNAME i USOS_PASSWORD")
        sys.exit(1)

    if not SCHEDULE_FILE.exists():
        print(f"BLAD: Brak pliku {SCHEDULE_FILE}")
        print("Pobierz plan z USOS (Moj plan -> Eksportuj do kalendarza) i zapisz jako plan.ics")
        sys.exit(1)

    # 1. Load schedule from ICS
    print(f"Wczytuje plan z {SCHEDULE_FILE.name}...")
    schedule = load_schedule_from_ics(SCHEDULE_FILE)

    # 2. Login
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    if not cas_login(session):
        send_notification("❌ Błąd logowania USOS", "Nie udało się zalogować. Sprawdź credentials.", 0xFF0000)
        sys.exit(1)

    # 3. Scrape
    if not REGISTRATIONS:
        print("BLAD: Lista REGISTRATIONS jest pusta. Odkomentuj przynajmniej jedną rejestrację.")
        sys.exit(1)

    all_groups = []
    for reg in REGISTRATIONS:
        rej_kod = reg["rej_kod"]
        print(f"\n=== {reg['name']} ({rej_kod}) ===")
        subjects = get_subjects(session, rej_kod)
        for i, subject in enumerate(subjects, 1):
            print(f"  [{i}/{len(subjects)}] {subject['name']}")
            all_groups.extend(get_groups(session, subject, rej_kod, CDYD_KOD))
            time.sleep(0.3)

    print(f"\nGrup lacznie: {len(all_groups)}")

    # 4. Filter conflicts
    available = []
    for g in all_groups:
        g["wolne"] = g["limit"] - g["zapisanych"]
        if not has_conflict(g, schedule):
            available.append(g)

    available_with_spots = [g for g in available if g["wolne"] > 0]
    print(f"Bez kolizji: {len(available)}, z wolnymi miejscami: {len(available_with_spots)}")

    # 5. Build state
    current_state = {
        group_key(g): {k: g[k] for k in
            ("przedmiot", "grupa", "dzien", "godz_start", "godz_end",
             "prowadzacy", "miejsce", "zapisanych", "limit", "wolne")}
        for g in available
    }

    # 6. Compare
    prev_state = load_previous_state(STATE_FILE)
    newly_available, spots_changed, newly_full = [], [], []

    for key, cur in current_state.items():
        prev = prev_state.get(key)
        if prev is None:
            if cur["wolne"] > 0:
                newly_available.append(cur)
        else:
            prev_wolne = prev.get("wolne", 0)
            if cur["wolne"] > 0 and prev_wolne == 0:
                newly_available.append(cur)
            elif cur["wolne"] == 0 and prev_wolne > 0:
                newly_full.append(cur)
            elif cur["wolne"] != prev_wolne and cur["wolne"] > 0:
                spots_changed.append(cur)

    print(f"Zmiany: +{len(newly_available)} nowych, {len(spots_changed)} zmian, {len(newly_full)} zapelnionych")

    # 7. Notify
    def group_field(g, prev_wolne=None):
        value = (f"📅 {g['dzien']} {g['godz_start']}-{g['godz_end']}\n"
                 f"👤 {g['prowadzacy']}\n")
        if prev_wolne is not None:
            value += f"💺 {prev_wolne} → **{g['wolne']}** wolnych ({g['zapisanych']}/{g['limit']})"
        else:
            value += f"💺 **{g['wolne']}** wolnych ({g['zapisanych']}/{g['limit']})"
        return {"name": f"{g['przedmiot']} (gr. {g['grupa']})", "value": value, "inline": False}

    if newly_available:
        send_notification(
            f"🎉 Nowe wolne miejsca! ({len(newly_available)} grup)",
            "Pojawiły się wolne miejsca bez kolizji z Twoim planem:",
            0x00FF00,
            [group_field(g) for g in newly_available[:25]],
        )

    if newly_full:
        send_notification(
            f"🔴 Zapełnione ({len(newly_full)})",
            ", ".join(f"{g['przedmiot']} gr.{g['grupa']}" for g in newly_full[:10]),
            0xFF6600,
        )

    if spots_changed:
        send_notification(
            f"🔄 Zmiana miejsc ({len(spots_changed)})",
            "Zmieniła się liczba wolnych miejsc:",
            0x3399FF,
            [group_field(g, prev_state.get(group_key(g), {}).get("wolne")) for g in spots_changed[:25]],
        )

    if not newly_available and not newly_full and not spots_changed:
        print("Brak zmian od ostatniego sprawdzenia.")

    # 8. Summary
    if available_with_spots:
        print("\n--- Dostepne (bez kolizji, wolne miejsca) ---")
        for g in available_with_spots:
            print(f"  {g['przedmiot']} gr.{g['grupa']} | "
                  f"{g['dzien']} {g['godz_start']}-{g['godz_end']} | "
                  f"{g['prowadzacy']} | {g['wolne']} wolnych ({g['zapisanych']}/{g['limit']})")
    else:
        print("\nBrak dostepnych grup.")

    save_state(STATE_FILE, current_state)
    print(f"Stan zapisany do {STATE_FILE.name}")


if __name__ == "__main__":
    main()

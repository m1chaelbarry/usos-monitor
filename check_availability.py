"""
USOS Language Course Availability Monitor
Checks for available spots in "Jezyki od podstaw (M1)" groups,
filters out schedule conflicts, compares with previous state,
and sends Discord DM notifications on changes.

GitHub Secrets required:
  USOS_USERNAME      - USOS login
  USOS_PASSWORD      - USOS password
  DISCORD_BOT_TOKEN  - Discord bot token
  DISCORD_USER_ID    - Your Discord user ID (for DMs)
"""

import csv
import json
import os
import re
import sys
import time
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

REGISTRATION = {
    "rej_kod": "6420-1000-2026L-A1M1",
    "cdyd_kod": "2026L",
    "name": "Jezyki od podstaw (M1)",
}

SCHEDULE_FILE = Path(__file__).parent / "plan.csv"
STATE_FILE = Path(__file__).parent / "previous_state.json"

USERNAME = os.environ.get("USOS_USERNAME", "")
PASSWORD = os.environ.get("USOS_PASSWORD", "")
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_USER_ID = os.environ.get("DISCORD_USER_ID", "")


# --- Discord Bot DM ---
DISCORD_API = "https://discord.com/api/v10"


def discord_dm(content=None, embed=None):
    """Send a DM to the user via Discord bot."""
    if not DISCORD_BOT_TOKEN or not DISCORD_USER_ID:
        print("  [SKIP] Brak DISCORD_BOT_TOKEN lub DISCORD_USER_ID")
        return

    headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json",
    }

    # Create/get DM channel
    try:
        resp = requests.post(
            f"{DISCORD_API}/users/@me/channels",
            json={"recipient_id": DISCORD_USER_ID},
            headers=headers,
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"  Discord: błąd tworzenia DM channel: {resp.status_code} {resp.text[:200]}")
            return
        channel_id = resp.json()["id"]
    except Exception as e:
        print(f"  Discord: wyjątek DM channel - {e}")
        return

    # Send message
    payload = {}
    if content:
        payload["content"] = content
    if embed:
        payload["embeds"] = [embed]

    try:
        resp = requests.post(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            json=payload,
            headers=headers,
            timeout=10,
        )
        if resp.status_code in (200, 201):
            print("  Discord DM: wysłano")
        else:
            print(f"  Discord DM: błąd {resp.status_code} - {resp.text[:200]}")
    except Exception as e:
        print(f"  Discord DM: wyjątek - {e}")


def send_notification(title, description, color=0x00FF00, fields=None):
    """Build Discord embed and send as DM."""
    embed = {
        "title": title,
        "description": description,
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "USOS Monitor"},
    }
    if fields:
        embed["fields"] = fields[:25]
    discord_dm(embed=embed)


# --- CAS Login ---
def cas_login(session):
    """Login to USOS via CAS PW authentication."""
    from urllib.parse import urlparse
    print("Logowanie do USOS via CAS...")

    login_url = f"{BASE_URL}?_action=logowanie"
    resp = session.get(login_url, allow_redirects=True)
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
                cas_link = content.split("url=", 1)[-1].split("URL=", 1)[-1].strip("'\" ")
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

    login_data = {
        "username": USERNAME,
        "password": PASSWORD,
        "execution": execution,
        "_eventId": "submit",
        "geolocation": "",
    }

    resp = session.post(form_action, data=login_data, allow_redirects=True)
    resp.encoding = "utf-8"

    if "wyloguj" in resp.text.lower() or "zalogowany" in resp.text.lower():
        print("  OK - zalogowano!")
        return True

    check = session.get(f"{BASE_URL}?_action=dla_stud/rejestracja/kalendarz")
    check.encoding = "utf-8"
    if "wyloguj" in check.text.lower() or "kalendarz rejestracji" in check.text.lower():
        print("  OK - zalogowano!")
        return True

    print("  BLAD: Nie udalo sie zalogowac")
    return False


# --- Scraping ---
def get_subjects(session, rej_kod):
    url = f"{BASE_URL}?_action=dla_stud/rejestracja/brdg2/wyborPrzedmiotu&rej_kod={rej_kod}"
    resp = session.get(url)
    soup = BeautifulSoup(resp.text, "html.parser")
    subjects = []
    for row in soup.select("tr[id]"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        link = cells[0].find("a")
        if not link:
            continue
        subjects.append({"name": link.get_text(strip=True), "code": row.get("id", "")})
    print(f"  Znaleziono {len(subjects)} przedmiotow")
    return subjects


def get_groups(session, subject, rej_kod, cdyd_kod):
    url = (
        f"{BASE_URL}?_action=dla_stud/rejestracja/brdg2/grupyPrzedmiotu"
        f"&rej_kod={rej_kod}&prz_kod={subject['code']}"
        f"&cdyd_kod={cdyd_kod}&odczyt=1&showLocationColumn=on&formFlag=1"
    )
    resp = session.get(url)
    soup = BeautifulSoup(resp.text, "html.parser")
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
        headers = header_row.find_all(["th", "td"])
        for i, h in enumerate(headers):
            text = h.find(string=True, recursive=False)
            text = text.strip().lower() if text else h.get_text(strip=True).lower()
            for key, pattern in [
                ("grupa", "grupa"), ("prowadzacy", "prowadz"), ("termin", "termin"),
                ("miejsce", "miejsce"), ("opis", "opis"), ("zapisanych", "zapisanych"),
            ]:
                if pattern in text:
                    col_map[key] = i
            if "limit" in text and "rn" in text:
                col_map["limit"] = i

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
            if idx is not None and idx < len(cells):
                return cells[idx].get_text(separator=" ", strip=True)
            return default

        grupa = get_cell("grupa")
        prowadzacy = get_cell("prowadzacy")
        termin = get_cell("termin")
        miejsce = get_cell("miejsce")
        opis = get_cell("opis")
        zapisanych_str = get_cell("zapisanych", "0")
        limit_str = get_cell("limit", "0")

        if not termin and not prowadzacy:
            continue

        try:
            zapisanych = int(re.sub(r"[^\d]", "", zapisanych_str) or "0")
        except ValueError:
            zapisanych = 0
        try:
            limit = int(re.sub(r"[^\d]", "", limit_str) or "0")
        except ValueError:
            limit = 0

        termin_matches = re.findall(r"(\w+)\s+(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", termin or "")
        if termin_matches:
            for dzien, godz_start, godz_end in termin_matches:
                groups.append({
                    "przedmiot": subject["name"],
                    "kod_przedmiotu": subject["code"],
                    "grupa": grupa,
                    "prowadzacy": prowadzacy,
                    "dzien": dzien,
                    "godz_start": godz_start,
                    "godz_end": godz_end,
                    "miejsce": miejsce,
                    "opis": opis,
                    "zapisanych": zapisanych,
                    "limit": limit,
                })
    return groups


# --- Schedule conflict detection ---
def parse_time(t):
    parts = t.strip().split(":")
    return int(parts[0]) * 60 + int(parts[1])


def load_schedule(csv_path):
    schedule = []
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            dzien = row.get("Dzień", "").strip()
            godziny = row.get("Godziny", "").strip()
            if not dzien or not godziny:
                continue
            match = re.match(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", godziny)
            if match:
                schedule.append((dzien, parse_time(match.group(1)), parse_time(match.group(2))))
    return schedule


def has_conflict(group, schedule):
    try:
        g_start = parse_time(group["godz_start"])
        g_end = parse_time(group["godz_end"])
    except (ValueError, KeyError):
        return False
    g_dzien = group.get("dzien", "").strip()
    for s_dzien, s_start, s_end in schedule:
        if g_dzien == s_dzien and g_start < s_end and g_end > s_start:
            return True
    return False


# --- State comparison ---
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
        print("BLAD: Ustaw USOS_USERNAME i USOS_PASSWORD")
        sys.exit(1)

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })

    if not cas_login(session):
        send_notification("❌ Błąd logowania USOS", "Nie udało się zalogować. Sprawdź credentials.", 0xFF0000)
        sys.exit(1)

    # Scrape groups
    rej_kod = REGISTRATION["rej_kod"]
    cdyd_kod = REGISTRATION["cdyd_kod"]
    print(f"\n=== {REGISTRATION['name']} ({rej_kod}) ===")

    subjects = get_subjects(session, rej_kod)
    if not subjects:
        print("Nie znaleziono przedmiotow.")
        sys.exit(1)

    all_groups = []
    for i, subject in enumerate(subjects, 1):
        print(f"  [{i}/{len(subjects)}] {subject['name']}")
        groups = get_groups(session, subject, rej_kod, cdyd_kod)
        all_groups.extend(groups)
        time.sleep(0.3)

    print(f"\nLaczna liczba grup: {len(all_groups)}")

    # Filter conflicts
    schedule = load_schedule(SCHEDULE_FILE)
    print(f"Plan zajec: {len(schedule)} wpisow")

    available = []
    for g in all_groups:
        g["wolne"] = g["limit"] - g["zapisanych"]
        if not has_conflict(g, schedule):
            available.append(g)

    available_with_spots = [g for g in available if g["wolne"] > 0]
    print(f"Bez kolizji: {len(available)}, z wolnymi miejscami: {len(available_with_spots)}")

    # Build current state
    current_state = {}
    for g in available:
        key = group_key(g)
        current_state[key] = {
            "przedmiot": g["przedmiot"],
            "grupa": g["grupa"],
            "dzien": g["dzien"],
            "godz_start": g["godz_start"],
            "godz_end": g["godz_end"],
            "prowadzacy": g["prowadzacy"],
            "miejsce": g["miejsce"],
            "zapisanych": g["zapisanych"],
            "limit": g["limit"],
            "wolne": g["wolne"],
        }

    # Compare states
    prev_state = load_previous_state(STATE_FILE)
    newly_available = []
    spots_changed = []
    newly_full = []

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

    print(f"\nZmiany: +{len(newly_available)} nowych, {len(spots_changed)} zmian, {len(newly_full)} zapełnionych")

    # Send notifications
    if newly_available:
        fields = []
        for g in newly_available[:25]:
            fields.append({
                "name": f"🟢 {g['przedmiot']} (gr. {g['grupa']})",
                "value": (
                    f"📅 {g['dzien']} {g['godz_start']}-{g['godz_end']}\n"
                    f"👤 {g['prowadzacy']}\n"
                    f"💺 **{g['wolne']}** wolnych ({g['zapisanych']}/{g['limit']})"
                ),
                "inline": False,
            })
        send_notification(
            f"🎉 Nowe wolne miejsca! ({len(newly_available)} grup)",
            "Pojawiły się wolne miejsca w lektoratach bez kolizji z Twoim planem:",
            0x00FF00, fields,
        )

    if newly_full:
        names = ", ".join(f"{g['przedmiot']} gr.{g['grupa']}" for g in newly_full[:10])
        send_notification(f"🔴 Zapełnione ({len(newly_full)})", names, 0xFF6600)

    if spots_changed:
        fields = []
        for g in spots_changed[:25]:
            prev = prev_state.get(group_key(g), {})
            fields.append({
                "name": f"🔄 {g['przedmiot']} (gr. {g['grupa']})",
                "value": (
                    f"📅 {g['dzien']} {g['godz_start']}-{g['godz_end']}\n"
                    f"💺 {prev.get('wolne', '?')} → **{g['wolne']}** wolnych"
                ),
                "inline": False,
            })
        send_notification(f"🔄 Zmiana miejsc ({len(spots_changed)})", "Zmieniła się liczba wolnych miejsc:", 0x3399FF, fields)

    if not newly_available and not newly_full and not spots_changed:
        print("Brak zmian od ostatniego sprawdzenia.")

    # Summary
    if available_with_spots:
        print("\n--- Dostępne (bez kolizji, wolne miejsca) ---")
        for g in available_with_spots:
            print(f"  {g['przedmiot']} gr.{g['grupa']} | "
                  f"{g['dzien']} {g['godz_start']}-{g['godz_end']} | "
                  f"{g['prowadzacy']} | {g['wolne']} wolnych ({g['zapisanych']}/{g['limit']})")
    else:
        print("\nBrak dostępnych grup.")

    save_state(STATE_FILE, current_state)
    print(f"Stan zapisany do {STATE_FILE}")


if __name__ == "__main__":
    main()

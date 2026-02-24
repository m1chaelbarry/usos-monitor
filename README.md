# USOS Monitor — Monitor wolnych miejsc w lektoratach

Automatycznie sprawdza dostępność miejsc w lektoratach **Języki od podstaw (M1)** na USOS PW i wysyła powiadomienia na Discord, gdy pojawią się wolne miejsca bez kolizji z Twoim planem.

## Jak uruchomić (fork & go)

### 1. Zrób fork tego repo

Kliknij **Fork** w prawym górnym rogu na GitHub.

### 2. Wrzuć swój plan zajęć

1. Wejdź na [USOS PW](https://usosweb.usos.pw.edu.pl) → **Mój plan** → **Eksportuj do kalendarza** → pobierz plik `.ics`
2. Zmień nazwę pliku na **`plan.ics`** i wrzuć do głównego folderu repo (zastąp istniejący)

### 3. Dodaj GitHub Secrets

W swoim forku: **Settings → Secrets and variables → Actions → New repository secret**

| Secret              | Wartość                                                                                                          |
| ------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `USOS_USERNAME`     | Twój nr albumu (np. `338413`)                                                                                    |
| `USOS_PASSWORD`     | Hasło do USOS                                                                                                    |
| `DISCORD_BOT_TOKEN` | [discord.com/developers](https://discord.com/developers/applications) → Twój bot → Bot → **Reset Token**         |
| `DISCORD_USER_ID`   | Discord → Ustawienia → Zaawansowane → włącz **Tryb programisty** → PPM na swoim nicku → **Kopiuj identyfikator** |

> [!NOTE]
> Bot musi być na wspólnym serwerze z Tobą, żeby móc wysyłać DM.

### 4. Gotowe!

Workflow odpala się **co 15 minut** automatycznie. Możesz też uruchomić ręcznie:  
**Actions → Check USOS Availability → Run workflow**

---

## Jak to działa

1. Parsuje `plan.ics` → wykrywa regularne zajęcia (≥3 wystąpień w semestrze, jednorazowe pomija)
2. Loguje się do USOS przez CAS PW
3. Pobiera wszystkie grupy z rejestracji "Języki od podstaw (M1)"
4. Filtruje grupy kolidujące z Twoim planem
5. Porównuje z poprzednim stanem → wykrywa zmiany
6. Wysyła DM na Discordzie:
   - 🟢 Nowe wolne miejsca
   - 🔄 Zmiana liczby wolnych miejsc
   - 🔴 Grupa się zapełniła

Brak zmian = brak powiadomień.

---

## Uruchomienie lokalne (opcjonalne)

```bash
pip install requests beautifulsoup4
```

```powershell
# Windows PowerShell
$env:USOS_USERNAME="123456"
$env:USOS_PASSWORD="haslo"
$env:DISCORD_BOT_TOKEN="token"
$env:DISCORD_USER_ID="twoje_id"
python check_availability.py
```

```bash
# Linux / macOS
export USOS_USERNAME=123456
export USOS_PASSWORD=haslo
export DISCORD_BOT_TOKEN=token
export DISCORD_USER_ID=twoje_id
python check_availability.py
```

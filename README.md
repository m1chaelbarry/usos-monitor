# USOS Monitor

Monitors available spots in USOS language courses ("Języki od podstaw M1") and sends Discord DM notifications when spots open up.

## Setup

### GitHub Secrets
Add these secrets in your repo → Settings → Secrets and variables → Actions:

| Secret              | Description            |
| ------------------- | ---------------------- |
| `USOS_USERNAME`     | USOS login (nr albumu) |
| `USOS_PASSWORD`     | USOS password          |
| `DISCORD_BOT_TOKEN` | Discord bot token      |
| `DISCORD_USER_ID`   | Your Discord user ID   |

### How to get Discord User ID
1. Open Discord → User Settings → Advanced → Enable **Developer Mode**
2. Right-click your username → **Copy User ID**

### Schedule
- Runs automatically every 15 minutes via GitHub Actions
- Can be triggered manually from Actions tab → "Check USOS Availability" → Run workflow

## How it works
1. Logs into USOS via CAS authentication
2. Scrapes all groups from "Języki od podstaw (M1)" registration
3. Filters out groups that conflict with your schedule (`plan.csv`)
4. Compares with previous state to detect changes
5. Sends Discord DM if new spots appear or groups fill up

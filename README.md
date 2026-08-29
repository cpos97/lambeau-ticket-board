# Lambeau Ticket Board

Price watcher for **Cowboys at Packers — Sun Oct 18, 2026, 7:20 PM CT, Lambeau Field**.

Runs entirely on GitHub's servers. Your computer does not need to be on.

- `watch.py` — scans Ticketmaster, decides whether anything qualifies, emails you
- `render.py` — rebuilds `dashboard.html` from the accumulated history
- `.github/workflows/scan.yml` — runs both every 30 minutes, commits the result,
  and republishes the site to GitHub Pages
- `history.jsonl` — the database: one line per scan, forever
- `img/icebowl.jpg` — page background (Ice Bowl, 1967; see `img/CREDITS.md`)

## Setup

Set four repository secrets. The CLI is the reliable route — it prompts you to
paste each value, so nothing is ever typed into a file or a URL:

```bash
gh secret set TM_API_KEY --repo cpos97/lambeau-ticket-board
gh secret set GMAIL_APP_PASSWORD --repo cpos97/lambeau-ticket-board
gh secret set EMAIL_FROM --repo cpos97/lambeau-ticket-board
gh secret set EMAIL_TO --repo cpos97/lambeau-ticket-board
```

Or in the browser: repo → **Settings** tab → sidebar **Secrets and variables** →
**Actions** → *New repository secret*. (The direct URL 404s unless you are signed
in as the repo owner.)

What each one is:

| Secret | What it is |
|---|---|
| `TM_API_KEY` | Ticketmaster Consumer Key — free at [developer.ticketmaster.com](https://developer-acct.ticketmaster.com/user/register) |
| `GMAIL_APP_PASSWORD` | 16-char app password from [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) — your normal password will not work |
| `EMAIL_FROM` | the Gmail address that sends the alert |
| `EMAIL_TO` | the address that receives it |

Then fire the first scan:

```bash
gh workflow run scan.yml --repo cpos97/lambeau-ticket-board
```

## Tuning

All thresholds live in `config.json` — edit and push, the next scan uses them.
Three independent alert tiers:

- **premium** — lower bowl sideline (111–128), alerts at or below `$450`,
  and on any new low since tracking began
- **good** — lower bowl anywhere (100–138), alerts at or below `$275`
- **steal** — any section, alerts under `$90` or on a drop of more than 45%
  below the 7-day low. This is the mispost catcher.

## What it does not do

Only Ticketmaster is scanned. StubHub, SeatGeek, Vivid Seats, TickPick and
Gametime are partner-API-only; scraping them violates their terms and their bot
protection breaks scrapers constantly. The board links straight to each of them
so you can cross-check by hand before buying — resale prices for identical seats
routinely differ 20–40% between sites.

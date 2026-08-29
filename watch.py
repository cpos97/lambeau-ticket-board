#!/usr/bin/env python3
"""
ticket-watch: price monitor for Cowboys @ Packers, Lambeau Field, 2026-10-18.

Data sources (in order of reliability):
  1. Ticketmaster Discovery API  -- official, documented, free key.
     Gives event-wide min/max all-in price. Refreshed hourly by TM.
  2. Ticketmaster ISM facets     -- powers the TM seat map. Per-section/row
     prices and quantities. Undocumented; may return 401/403 on some keys.
     Failure here is non-fatal; the script degrades to source 1.

Deliberately does NOT scrape StubHub / SeatGeek / Vivid Seats / TickPick.
Those are partner-API-only and scraping them violates their terms.

Usage:
  python3 watch.py               run one check, email if something qualifies
  python3 watch.py --dry-run     run one check, print only, never email
  python3 watch.py --test-email  send one test email and exit
  python3 watch.py --report      print price history, no network calls
"""

import json
import os
import ssl
import sys
import smtplib
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
HISTORY_PATH = os.path.join(HERE, "history.jsonl")
STATE_PATH = os.path.join(HERE, "state.json")
LATEST_PATH = os.path.join(HERE, "latest.json")
LOG_PATH = os.path.join(HERE, "watch.log")

DISCOVERY_URL = "https://app.ticketmaster.com/discovery/v2/events/{eid}.json"
ISM_URL = "https://services.ticketmaster.com/api/ismds/event/{eid}/facets"

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " \
     "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


# ---------------------------------------------------------------- utilities

def now():
    return datetime.now(timezone.utc)


def log(msg):
    line = "[{}] {}".format(now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        with open(LOG_PATH, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def load_config():
    """Config holds only non-secret settings. Secrets arrive via env vars so
    the repo can be public without leaking anything."""
    with open(CONFIG_PATH) as fh:
        cfg = json.load(fh)

    cfg["ticketmaster_api_key"] = os.environ.get("TM_API_KEY", "").strip()
    ec = cfg.setdefault("email", {})
    ec["app_password"] = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    ec["from_address"] = os.environ.get("EMAIL_FROM", "").strip()
    ec["to_address"] = os.environ.get("EMAIL_TO", "").strip() or ec["from_address"]

    if not cfg["ticketmaster_api_key"]:
        sys.exit("ERROR: TM_API_KEY is not set.\n"
                 "Locally:  export TM_API_KEY=...\n"
                 "On GitHub: add it under Settings > Secrets and variables > Actions.\n"
                 "Free key:  https://developer-acct.ticketmaster.com/user/register")
    return cfg


def parse_sections(spec):
    """['111-128', '140'] -> set of ints. ['*'] -> None (means: match anything)."""
    if not spec or "*" in spec:
        return None
    out = set()
    for item in spec:
        item = str(item).strip()
        if "-" in item:
            lo, hi = item.split("-", 1)
            try:
                out.update(range(int(lo), int(hi) + 1))
            except ValueError:
                continue
        else:
            try:
                out.add(int(item))
            except ValueError:
                continue
    return out


def section_num(raw):
    """'112' -> 112 ; 'Section 112' -> 112 ; '112A' -> 112 ; 'LEDGE' -> None"""
    digits = ""
    for ch in str(raw):
        if ch.isdigit():
            digits += ch
        elif digits:
            break
    return int(digits) if digits else None


def http_get_json(url, params, timeout=25):
    full = url + "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(full, headers={
        "User-Agent": UA,
        "Accept": "application/json",
    })
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


# ------------------------------------------------------------ data fetching

def fetch_discovery(eid, apikey):
    """Returns dict with min/max all-in price and the public event URL."""
    try:
        data = http_get_json(DISCOVERY_URL.format(eid=eid), {"apikey": apikey})
    except urllib.error.HTTPError as e:
        log("Discovery API HTTP {} -- {}".format(e.code, e.reason))
        return None
    except Exception as e:
        log("Discovery API failed: {}".format(e))
        return None

    ranges = data.get("priceRanges") or []
    mins = [r["min"] for r in ranges if isinstance(r.get("min"), (int, float))]
    maxs = [r["max"] for r in ranges if isinstance(r.get("max"), (int, float))]
    return {
        "name": data.get("name"),
        "url": data.get("url"),
        "min_price": min(mins) if mins else None,
        "max_price": max(maxs) if maxs else None,
        "status": (data.get("dates", {}).get("status", {}) or {}).get("code"),
    }


def fetch_ism_listings(eid, apikey):
    """
    Best-effort per-section inventory from the seat-map service.
    Returns list of {section, row, price, qty} or [] if unavailable.
    """
    params = {
        "by": "section+row+price+quantity",
        "show": "places+sections",
        "embed": ["offer", "description"],
        "q": "available",
        "apikey": apikey,
        "limit": 400,
    }
    try:
        data = http_get_json(ISM_URL.format(eid=eid), params)
    except urllib.error.HTTPError as e:
        log("ISM facets unavailable (HTTP {}). Falling back to event-wide "
            "min/max only.".format(e.code))
        return []
    except Exception as e:
        log("ISM facets failed ({}). Falling back to event-wide min/max only."
            .format(e))
        return []

    # Build offerId -> price map from the embedded offers, if present.
    offers = {}
    embedded = data.get("_embedded") or {}
    for off in (embedded.get("offer") or []):
        oid = off.get("offerId") or off.get("id")
        price = None
        for field in ("totalPrice", "listPrice", "faceValue", "price"):
            v = off.get(field)
            if isinstance(v, (int, float)):
                price = float(v)
                break
            if isinstance(v, dict) and isinstance(v.get("value"), (int, float)):
                price = float(v["value"])
                break
        if oid and price is not None:
            offers[oid] = price

    listings = []
    for facet in (data.get("facets") or []):
        secs = facet.get("section") or []
        rows = facet.get("row") or []
        qty = facet.get("count") or facet.get("quantity") or 0

        price = None
        raw_price = facet.get("price")
        if isinstance(raw_price, list) and raw_price:
            nums = [float(p) for p in raw_price
                    if isinstance(p, (int, float))]
            if nums:
                price = min(nums)
        elif isinstance(raw_price, (int, float)):
            price = float(raw_price)
        if price is None:
            oprices = [offers[o] for o in (facet.get("offers") or [])
                       if o in offers]
            if oprices:
                price = min(oprices)
        if price is None:
            continue

        listings.append({
            "section": str(secs[0]) if secs else "?",
            "row": str(rows[0]) if rows else "?",
            "price": round(price, 2),
            "qty": int(qty) if isinstance(qty, (int, float)) else 0,
        })

    log("ISM facets returned {} priced listings.".format(len(listings)))
    return listings


# -------------------------------------------------------------- history/state

def append_history(record):
    with open(HISTORY_PATH, "a") as fh:
        fh.write(json.dumps(record) + "\n")


def read_history():
    if not os.path.exists(HISTORY_PATH):
        return []
    out = []
    with open(HISTORY_PATH) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH) as fh:
                return json.load(fh)
        except ValueError:
            pass
    return {"all_time_low": {}, "last_alert": {}}


def save_state(state):
    with open(STATE_PATH, "w") as fh:
        json.dump(state, fh, indent=2)


def trailing_low(history, tier, days):
    cutoff = now() - timedelta(days=days)
    lows = []
    for rec in history:
        try:
            ts = datetime.fromisoformat(rec["ts"])
        except (KeyError, ValueError):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < cutoff:
            continue
        v = (rec.get("tier_lows") or {}).get(tier)
        if isinstance(v, (int, float)):
            lows.append(v)
    return min(lows) if lows else None


# ------------------------------------------------------------ alert decisions

def evaluate(cfg, listings, discovery, history, state):
    """Returns (alerts, tier_lows). alerts = list of dicts describing a hit."""
    qty_wanted = min(cfg.get("quantity") or [2])
    alerts = []
    tier_lows = {}

    for tier_key, tier in (cfg.get("targets") or {}).items():
        if tier_key.startswith("_"):
            continue
        allowed = parse_sections(tier.get("sections"))
        best = parse_sections(tier.get("best_sections"))

        matches = []
        for lst in listings:
            if lst["qty"] and lst["qty"] < qty_wanted:
                continue
            sn = section_num(lst["section"])
            if allowed is not None and (sn is None or sn not in allowed):
                continue
            matches.append(lst)

        # Fall back to the event-wide minimum when per-section data is absent.
        if not matches and not listings and discovery \
                and discovery.get("min_price") is not None:
            if allowed is None:  # only the wildcard 'steal' tier can use it
                matches = [{"section": "(event-wide)", "row": "-",
                            "price": discovery["min_price"], "qty": 0}]

        if not matches:
            continue

        matches.sort(key=lambda m: m["price"])
        low = matches[0]["price"]
        tier_lows[tier_key] = low

        prev_atl = state["all_time_low"].get(tier_key)
        threshold = tier.get("alert_at_or_below")
        reasons = []

        if isinstance(threshold, (int, float)) and low <= threshold:
            reasons.append("at or below your ${:.0f} target".format(threshold))

        if tier.get("alert_on_new_low") and prev_atl is not None \
                and low < prev_atl:
            reasons.append("new low since tracking began (was ${:.2f})"
                           .format(prev_atl))

        drop_factor = tier.get("drop_factor")
        if isinstance(drop_factor, (int, float)):
            tlow = trailing_low(history, tier_key,
                                tier.get("trailing_days", 7))
            if tlow and low < tlow * drop_factor:
                reasons.append(
                    "{:.0f}% below the {}-day low of ${:.2f} -- possible "
                    "mispricing".format((1 - low / tlow) * 100,
                                        tier.get("trailing_days", 7), tlow))

        if prev_atl is None or low < prev_atl:
            state["all_time_low"][tier_key] = low

        if not reasons:
            continue

        # Cooldown: same tier at the same price within N hours -> skip.
        cd_hours = (cfg.get("behavior") or {}).get("cooldown_hours", 6)
        ck = "{}:{:.2f}".format(tier_key, low)
        last = state["last_alert"].get(ck)
        if last:
            try:
                lt = datetime.fromisoformat(last)
                if lt.tzinfo is None:
                    lt = lt.replace(tzinfo=timezone.utc)
                if now() - lt < timedelta(hours=cd_hours):
                    log("Cooldown active for {} at ${:.2f}; not re-alerting."
                        .format(tier_key, low))
                    continue
            except ValueError:
                pass
        state["last_alert"][ck] = now().isoformat()

        top = matches[:8]
        for m in top:
            sn = section_num(m["section"])
            m["is_best"] = bool(best and sn in best)

        alerts.append({
            "tier": tier_key,
            "label": tier.get("label", tier_key),
            "low": low,
            "reasons": reasons,
            "listings": top,
            "count": len(matches),
        })

    return alerts, tier_lows


# -------------------------------------------------------------------- email

def build_email_body(cfg, alerts, discovery):
    ev = cfg["event"]
    lines = []
    lines.append("{} - {} - {}".format(ev["name"], ev["date"], ev["venue"]))
    if discovery:
        if discovery.get("min_price") is not None:
            lines.append("Event-wide all-in range: ${:.2f} - ${:.2f}".format(
                discovery["min_price"], discovery.get("max_price") or 0))
        if discovery.get("url"):
            lines.append(discovery["url"])
    lines.append("")
    lines.append("=" * 62)

    for a in alerts:
        lines.append("")
        lines.append("{}  --  low ${:.2f}".format(a["label"].upper(), a["low"]))
        for r in a["reasons"]:
            lines.append("  * {}".format(r))
        lines.append("  {} qualifying listing(s). Cheapest:".format(a["count"]))
        for m in a["listings"]:
            star = "  <-- prime sideline" if m.get("is_best") else ""
            lines.append("    ${:>8.2f}   Sec {:<6} Row {:<5} qty {}{}".format(
                m["price"], m["section"], m["row"], m["qty"] or "?", star))
        lines.append("-" * 62)

    lines.append("")
    lines.append("Ticketmaster data only. Before buying, cross-check the same "
                 "seats on StubHub, SeatGeek, Vivid Seats and TickPick -- "
                 "resale prices there often differ.")
    lines.append("")
    lines.append("Checked at {} UTC".format(now().strftime("%Y-%m-%d %H:%M")))
    return "\n".join(lines)


def send_email(cfg, subject, body):
    ec = cfg.get("email") or {}
    if not ec.get("enabled"):
        log("Email disabled in config; skipping send.")
        return False
    pw = ec.get("app_password", "")
    if not pw:
        log("GMAIL_APP_PASSWORD not set; skipping email (scan still recorded).")
        return False
    if not ec.get("from_address") or not ec.get("to_address"):
        log("EMAIL_FROM / EMAIL_TO not set; skipping email.")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = ec["from_address"]
    msg["To"] = ec["to_address"]
    msg.set_content(body)

    ctx = ssl.create_default_context()
    with smtplib.SMTP(ec["smtp_host"], ec["smtp_port"], timeout=30) as s:
        s.starttls(context=ctx)
        s.login(ec["from_address"], pw)
        s.send_message(msg)
    log("Email sent to {}".format(ec["to_address"]))
    return True


# --------------------------------------------------------------------- report

def do_report():
    hist = read_history()
    if not hist:
        print("No history yet. Run the script at least once.")
        return
    print("{:<20} {:>10} {:>10} {:>10} {:>10}".format(
        "timestamp (UTC)", "premium", "good", "steal", "event min"))
    print("-" * 64)
    for rec in hist[-60:]:
        tl = rec.get("tier_lows") or {}
        def f(v):
            return "${:.0f}".format(v) if isinstance(v, (int, float)) else "-"
        print("{:<20} {:>10} {:>10} {:>10} {:>10}".format(
            rec.get("ts", "")[:19].replace("T", " "),
            f(tl.get("premium")), f(tl.get("good")), f(tl.get("steal")),
            f(rec.get("event_min"))))
    lows = {}
    for rec in hist:
        for k, v in (rec.get("tier_lows") or {}).items():
            if isinstance(v, (int, float)):
                lows[k] = min(lows.get(k, v), v)
    print("\nAll-time lows seen: " + ", ".join(
        "{} ${:.2f}".format(k, v) for k, v in sorted(lows.items())) or "none")


# ----------------------------------------------------------------------- main

def main():
    args = set(sys.argv[1:])

    if "--report" in args:
        do_report()
        return

    cfg = load_config()

    if "--test-email" in args:
        ok = send_email(cfg, "[ticket-watch] test email",
                        "If you are reading this, email alerts are working.\n\n"
                        "Watching: {} on {} at {}.".format(
                            cfg["event"]["name"], cfg["event"]["date"],
                            cfg["event"]["venue"]))
        print("Test email sent." if ok else "Test email NOT sent -- see above.")
        return

    dry = "--dry-run" in args
    eid = cfg["event"]["ticketmaster_event_id"]
    key = cfg["ticketmaster_api_key"]

    discovery = fetch_discovery(eid, key)
    if discovery:
        log("Discovery: {} | status={} | ${} - ${}".format(
            discovery.get("name"), discovery.get("status"),
            discovery.get("min_price"), discovery.get("max_price")))
    listings = fetch_ism_listings(eid, key)

    if not discovery and not listings:
        log("No data retrieved from either source. Nothing to evaluate.")
        return

    history = read_history()
    state = load_state()
    alerts, tier_lows = evaluate(cfg, listings, discovery, history, state)

    append_history({
        "ts": now().isoformat(),
        "tier_lows": tier_lows,
        "event_min": (discovery or {}).get("min_price"),
        "event_max": (discovery or {}).get("max_price"),
        "listing_count": len(listings),
    })
    save_state(state)

    with open(LATEST_PATH, "w") as fh:
        json.dump({
            "ts": now().isoformat(),
            "tier_lows": tier_lows,
            "event_min": (discovery or {}).get("min_price"),
            "event_max": (discovery or {}).get("max_price"),
            "event_url": (discovery or {}).get("url"),
            "listings": sorted(listings, key=lambda l: l["price"])[:60],
            "alerts": [{"tier": a["tier"], "label": a["label"],
                        "low": a["low"], "reasons": a["reasons"]}
                       for a in alerts],
        }, fh, indent=2)

    if not alerts:
        log("No alert conditions met. Current tier lows: {}".format(
            tier_lows or "n/a"))
        return

    body = build_email_body(cfg, alerts, discovery)
    best = min(a["low"] for a in alerts)
    subject = "[Packers/Cowboys] ${:.0f} - {}".format(
        best, ", ".join(a["label"] for a in alerts))

    print("\n" + body + "\n")
    if dry:
        log("--dry-run: email suppressed.")
    else:
        try:
            send_email(cfg, subject, body)
        except Exception as e:
            log("Email send FAILED: {}".format(e))


if __name__ == "__main__":
    main()

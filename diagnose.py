#!/usr/bin/env python3
"""One-off probe: dump everything Discovery exposes for this game, so we can
see whether Ticketmaster resale inventory is reachable and where it lives."""
import json, os, sys, urllib.parse, urllib.request, ssl

KEY = os.environ.get("TM_API_KEY", "").strip()
if not KEY:
    sys.exit("TM_API_KEY not set")

UA = "ticket-watch-diagnose/1.0"
BASE = "https://app.ticketmaster.com/discovery/v2/"


def get(path, **params):
    params["apikey"] = KEY
    url = BASE + path + "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25,
                                    context=ssl.create_default_context()) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        print("  !! {} -> {}".format(path, e))
        return None


print("=" * 72)
print("1. EVERY Packers event in a 3-day window around 2026-10-18")
print("=" * 72)
d = get("events.json", keyword="Green Bay Packers", classificationName="Football",
        countryCode="US", startDateTime="2026-10-16T00:00:00Z",
        endDateTime="2026-10-20T23:59:59Z", size=50, includeTBA="yes",
        includeTBD="yes")
events = ((d or {}).get("_embedded") or {}).get("events") or []
print("found: {}".format(len(events)))
for e in events:
    st = ((e.get("dates") or {}).get("status") or {}).get("code")
    pr = e.get("priceRanges") or []
    prs = ", ".join("{}:{}-{}".format(p.get("type"), p.get("min"), p.get("max"))
                    for p in pr) or "none"
    print("  id={:<22} status={:<10} name={}".format(
        e.get("id"), str(st), (e.get("name") or "")[:44]))
    print("     priceRanges: {}".format(prs))
    print("     url: {}".format((e.get("url") or "")[:110]))

print()
print("=" * 72)
print("2. Full detail for the Cowboys event")
print("=" * 72)
target = None
for e in events:
    if "cowboy" in (e.get("name") or "").lower():
        target = e.get("id"); break
if not target:
    print("no cowboys event found"); sys.exit(0)

ev = get("events/{}.json".format(target))
if ev:
    print("top-level keys: {}".format(sorted(ev.keys())))
    for k in ("name", "id", "url", "priceRanges", "sales", "dates",
              "products", "ticketing", "seatmap", "accessibility",
              "ticketLimit", "outlets", "info", "pleaseNote"):
        if k in ev:
            print("\n--- {} ---".format(k))
            print(json.dumps(ev[k], indent=2)[:1400])
    links = ev.get("_links") or {}
    print("\n--- _links keys ---")
    print(sorted(links.keys()))
    emb = ev.get("_embedded") or {}
    print("--- _embedded keys ---")
    print(sorted(emb.keys()))

print()
print("=" * 72)
print("3. Does the API expose resale as its own source/segment?")
print("=" * 72)
for src in ("ticketmaster", "tmr", "universe", "frontgate"):
    r = get("events.json", keyword="Packers Cowboys", source=src,
            countryCode="US", startDateTime="2026-10-16T00:00:00Z",
            endDateTime="2026-10-20T23:59:59Z", size=10)
    n = (((r or {}).get("_embedded") or {}).get("events") or [])
    print("  source={:<14} -> {} event(s)".format(src, len(n)))
    for e in n:
        print("      {} | {} | {}".format(
            e.get("id"), ((e.get("dates") or {}).get("status") or {}).get("code"),
            (e.get("name") or "")[:40]))

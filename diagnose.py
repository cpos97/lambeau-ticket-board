#!/usr/bin/env python3
"""Probe the Ticketmaster seat-map (ISM) service using the HOST event id,
which is what that service expects -- not the Discovery id."""
import json, os, sys, urllib.parse, urllib.request, ssl

KEY = os.environ.get("TM_API_KEY", "").strip()
if not KEY:
    sys.exit("TM_API_KEY not set")

HOST_ID = "0700646BCF6088AD"      # from the event url / seatmap staticUrl
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
ISM = "https://services.ticketmaster.com/api/ismds/event/{}/facets".format(HOST_ID)


def probe(label, params):
    url = ISM + "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25,
                                    context=ssl.create_default_context()) as r:
            body = r.read().decode("utf-8", "replace")
            data = json.loads(body)
            print("\n### {}  -> HTTP {}".format(label, r.status))
            print("    top keys: {}".format(sorted(data.keys())[:12]))
            facets = data.get("facets") or []
            print("    facets: {}".format(len(facets)))
            for f in facets[:6]:
                print("      {}".format(json.dumps(f)[:230]))
            emb = data.get("_embedded") or {}
            print("    _embedded keys: {}".format(sorted(emb.keys())))
            for o in (emb.get("offer") or [])[:4]:
                print("      offer: {}".format(json.dumps(o)[:260]))
            if not facets and not emb:
                print("    raw head: {}".format(body[:400]))
            return True
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        print("\n### {}  -> HTTP {} {}".format(label, e.code, e.reason))
        print("    {}".format(detail))
    except Exception as e:
        print("\n### {}  -> {}".format(label, e))
    return False


print("=" * 72)
print("ISM seat-map probe, host id {}".format(HOST_ID))
print("=" * 72)

probe("A minimal", {"apikey": KEY, "q": "available"})

probe("B by section+price", {
    "apikey": KEY, "by": "section price", "show": "places",
    "q": "available", "limit": 20})

probe("C resale filter", {
    "apikey": KEY,
    "by": "grouping section row price tickettype offers",
    "show": "places inventoryTypes offers",
    "q": "and(available,resale)", "embed": ["offer", "description"],
    "limit": 20})

probe("D third-party-resale", {
    "apikey": KEY,
    "by": "section row price offers",
    "show": "places offers",
    "q": "and(available,third-party-resale)",
    "embed": ["offer", "description"], "limit": 20})

probe("E everything available", {
    "apikey": KEY,
    "by": "grouping section row seats price tickettype offers description",
    "show": "places inventoryTypes offers description",
    "q": "available", "embed": ["offer", "description"],
    "limit": 30, "mode": "primary:eda"})

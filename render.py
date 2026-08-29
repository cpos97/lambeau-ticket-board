#!/usr/bin/env python3
"""
Renders dashboard.html from whatever watch.py has collected so far.
Safe to run at any time -- with zero scans it produces an honest empty board.
"""

import html
import json
import os
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
P = lambda n: os.path.join(HERE, n)

TIER_ORDER = ["target", "steal"]
TIER_TOKEN = {
    "target": ("--gb-gold", "#FFB612"),
    "steal":  ("--dal-silver", "#8B98A6"),
}

SITES = [
    ("Ticketmaster", "Official + verified resale. The scanner reads this one.",
     "https://www.ticketmaster.com/green-bay-packers-vs-dallas-cowboys-green-bay-wisconsin-10-18-2026/event/0700646BCF6088AD"),
    ("Vivid Seats", "Resale. Often the widest lower-bowl inventory.",
     "https://www.vividseats.com/packers-vs-cowboys-tickets--sports-nfl-football/matchup/339-214"),
    ("StubHub", "Resale. Fees load late in checkout.",
     "https://www.stubhub.com/find/s/?q=Packers%20Cowboys%20Lambeau"),
    ("SeatGeek", "Resale. Shows a deal score per listing.",
     "https://seatgeek.com/search?q=packers%20cowboys"),
    ("TickPick", "Resale, no buyer fees. Often the true low.",
     "https://www.tickpick.com/search/?q=packers%20cowboys"),
    ("Gametime", "Resale. Strong on last-minute drops.",
     "https://gametime.co/search?q=packers%20cowboys"),
]


def esc(v):
    return html.escape(str(v), quote=True)


def read_json(name, default):
    path = P(name)
    if not os.path.exists(path):
        return default
    try:
        with open(path) as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return default


def read_history():
    path = P("history.jsonl")
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def fmt_ts(iso, fallback="never"):
    if not iso:
        return fallback
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%b %-d, %-I:%M %p")
    except ValueError:
        return fallback


# ------------------------------------------------------------------ builders

def build_tiers(cfg, latest, state):
    tier_lows = (latest or {}).get("tier_lows") or {}
    hit_tiers = {a["tier"] for a in ((latest or {}).get("alerts") or [])}
    cards = []

    for key in TIER_ORDER:
        tier = (cfg.get("targets") or {}).get(key)
        if not tier:
            continue
        low = tier_lows.get(key)
        atl = (state.get("all_time_low") or {}).get(key)
        is_hit = key in hit_tiers
        target = tier.get("alert_at_or_below")

        if is_hit:
            pill = '<span class="pill hit">Deal &mdash; emailed</span>'
        elif low is None:
            pill = '<span class="pill none">No data yet</span>'
        else:
            pill = '<span class="pill">Watching</span>'

        if low is None:
            price = '<div class="price empty">&mdash;</div>'
        else:
            price = ('<div class="price"><span class="cur">$</span>%s</div>'
                     % ("{:,.0f}".format(low)))

        if key == "steal":
            meta = ("Any section, any level. Fires when a price lands under "
                    "<code>${:,.0f}</code> or drops more than {:.0f}% below the "
                    "{}-day low &mdash; the shape a mispost takes.".format(
                        target or 0,
                        (1 - tier.get("drop_factor", .55)) * 100,
                        tier.get("trailing_days", 7)))
        else:
            meta = ("Lowest all-in price found across every source that has a "
                    "usable feed. Alerts at or below <code>${:,.0f}</code>, and "
                    "on any new low since tracking began.".format(target or 0))

        foot = ("Lowest ever seen &nbsp;<b class=\"mono\">${:,.2f}</b>".format(atl)
                if isinstance(atl, (int, float))
                else "No baseline recorded yet")

        cards.append(
            '<article class="card tier{hit}">'
            '<div class="tier-top"><div>'
            '<div class="tier-label">{lab}</div><h3>{name}</h3>'
            '</div>{pill}</div>{price}'
            '<div class="tier-meta">{meta}</div>'
            '<div class="tier-foot">{foot}</div></article>'.format(
                hit=" hit" if is_hit else "",
                lab={"target": "Live across every source",
                 "steal": "Mispost watch"}.get(key, key),
                name=esc(tier.get("label", key)),
                pill=pill, price=price, meta=meta, foot=foot))

    return "\n".join(cards) or '<p class="empty-note">No tiers configured.</p>'


def build_chart(history):
    usable = [h for h in history if (h.get("tier_lows") or h.get("event_min"))]
    if len(usable) < 2:
        body = ('<div class="empty-note">A trend needs at least two scans.'
                '<br>Logged so far: <b>{}</b>.</div>'.format(len(usable)))
        return body, {"labels": [], "series": []}

    labels = [fmt_ts(h.get("ts"), "?") for h in usable]
    series, legend = [], []
    for key in TIER_ORDER:
        pts = [{"v": (h.get("tier_lows") or {}).get(key)} for h in usable]
        if not any(p["v"] is not None for p in pts):
            continue
        token, fallback = TIER_TOKEN[key]
        series.append({"label": key, "points": pts, "token": token,
                       "fallback": fallback, "fill": key == "premium"})
        legend.append(
            '<span><span class="swatch" style="background:var({})"></span>{}</span>'
            .format(token, esc({"premium": "Lower bowl sideline",
                                "good": "Lower bowl (any)",
                                "steal": "Cheapest anywhere"}[key])))

    body = ('<canvas id="spark" role="img" aria-label="Lowest ticket price by '
            'tier across every scan"></canvas><div class="legend">{}</div>'
            .format("".join(legend)))
    return body, {"labels": labels, "series": series}


def build_range(latest):
    srcs = (latest or {}).get("sources") or []
    if srcs:
        cards = []
        for s_ in srcs:
            lo = s_.get("low")
            hi = s_.get("high")
            cards.append(
                '<div class="range-item"><span class="k">{}</span>'
                '<span class="v">${:,.0f}</span>'
                '<span style="font-size:.72rem;color:var(--ink-3)">{}</span>'
                '</div>'.format(
                    esc(s_.get("source", "?")),
                    lo if isinstance(lo, (int, float)) else 0,
                    ("up to ${:,.0f}".format(hi)
                     if isinstance(hi, (int, float)) and hi else "cheapest all-in")))
        note = ('<p style="width:100%;margin:14px 0 0;font-size:.8rem;'
                'color:var(--ink-3);line-height:1.6">Ticketmaster, StubHub, '
                'TickPick and Vivid Seats have no public price feed, so they '
                'cannot be scanned \u2014 use the cross-check links below for '
                'those.</p>')
        return '<div class="card range">' + "".join(cards) + note + '</div>'

    return _build_status_panel(latest)


def _build_status_panel(latest):
    status = (latest or {}).get("status")
    if status and status != "onsale":
        onsale = (latest or {}).get("onsale_start")
        when = fmt_ts(onsale, "") if onsale else ""
        if (latest or {}).get("sale_tbd") or not when:
            line = ("Ticketmaster has not announced a public on-sale date for "
                    "this game yet.")
        else:
            line = "Public on-sale is listed for <b>{}</b>.".format(esc(when))
        label = {"offsale": "Not on sale",
                 "cancelled": "Cancelled",
                 "postponed": "Postponed",
                 "rescheduled": "Rescheduled"}.get(status, esc(status))
        return ('<div class="card range" style="display:block">'
                '<div style="display:flex;align-items:center;gap:12px;'
                'margin-bottom:10px">'
                '<span class="pill" style="background:var(--sunk)">{}</span>'
                '<span class="k" style="font-size:.66rem;letter-spacing:.16em;'
                'text-transform:uppercase;color:var(--ink-3);font-weight:600">'
                'Ticketmaster status</span></div>'
                '<p style="margin:0;font-size:.92rem;color:var(--ink-2);'
                'max-width:62ch;line-height:1.6">{} The scanner keeps checking '
                'every 30 minutes and will email you the moment it flips on '
                'sale. Resale sites in Cross-Check below may already have '
                'listings.</p></div>').format(label, line)

    return _build_range_prices(latest)


def _build_range_prices(latest):
    lo = (latest or {}).get("event_min")
    hi = (latest or {}).get("event_max")
    if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
        return ('<div class="card"><div class="empty-note">No range reported yet. '
                'The Discovery API returns a min and max once the scanner has '
                'run with a valid key.</div></div>')
    spread = hi - lo
    pct = 0 if hi <= 0 else max(4, min(100, (lo / hi) * 100))
    return ('<div class="card range">'
            '<div class="range-item"><span class="k">Cheapest</span>'
            '<span class="v">${:,.0f}</span></div>'
            '<div class="range-item"><span class="k">Most expensive</span>'
            '<span class="v">${:,.0f}</span></div>'
            '<div class="range-item"><span class="k">Spread</span>'
            '<span class="v">${:,.0f}</span></div>'
            '<div class="range-bar"><div class="range-track">'
            '<div class="range-fill" style="width:{:.1f}%"></div></div>'
            '<div class="range-cap"><span>floor</span>'
            '<span>ceiling</span></div></div>'
            '</div>').format(lo, hi, spread, pct)


def build_table(latest):
    rows = (latest or {}).get("listings") or []
    if not rows:
        return ('<div class="empty-note">'
                'Seat-by-seat detail is not available on a public developer key.'
                '<br>Ticketmaster\'s seat-map service holds the resale listings '
                'you see on their website, but it requires a service token '
                'issued only to their own apps.<br>Use the cross-check links '
                'below for live resale prices.</div>')
    rows = sorted(rows, key=lambda r: r.get("price", 9e9))[:25]
    out = ['<table><thead><tr>'
           '<th>Price</th><th>Section</th><th>Row</th><th>Together</th>'
           '<th>Tier</th></tr></thead><tbody>']
    for r in rows:
        sec = r.get("section", "?")
        try:
            n = int("".join(c for c in str(sec) if c.isdigit()) or -1)
        except ValueError:
            n = -1
        if 114 <= n <= 120:
            tier, prime = "Lower bowl sideline", '<span class="prime">Prime</span>'
        elif 111 <= n <= 128:
            tier, prime = "Lower bowl sideline", ""
        elif 100 <= n <= 138:
            tier, prime = "Lower bowl end zone", ""
        else:
            tier, prime = "Upper / other", ""
        out.append(
            '<tr><td class="num">${:,.2f}</td><td class="num">{}{}</td>'
            '<td class="num">{}</td><td class="num">{}</td><td>{}</td></tr>'
            .format(r.get("price", 0), esc(sec), prime, esc(r.get("row", "?")),
                    esc(r.get("qty") or "?"), tier))
    out.append("</tbody></table>")
    return "".join(out)


def build_sites():
    return "\n".join(
        '<a class="card site" href="{u}" target="_blank" rel="noopener noreferrer">'
        '<span class="site-name">{n}</span>'
        '<span class="site-sub">{s}</span></a>'.format(u=esc(u), n=esc(n), s=esc(s))
        for n, s, u in SITES)


# ---------------------------------------------------------------------- main

def main():
    cfg = read_json("config.json", {})
    state = read_json("state.json", {})
    latest = read_json("latest.json", {})
    history = read_history()

    with open(P("template.html")) as fh:
        tpl = fh.read()

    if False:
        sclass, stext = "off", ""
    elif history:
        n_alerts = len((latest or {}).get("alerts") or [])
        st = (latest or {}).get("status")
        if n_alerts:
            sclass, stext = "wait", "{} alert{} on last scan".format(
                n_alerts, "" if n_alerts == 1 else "s")
        elif st and st != "onsale":
            sclass, stext = "off", "Not on sale at Ticketmaster yet"
        else:
            sclass, stext = "ok", "Scanning &mdash; nothing at target"
    else:
        sclass, stext = "off", "Standing by for the first scan"

    chart_body, chart_data = build_chart(history)

    # The address lives only in GitHub secrets. Mask it so a public page
    # never carries a full email address.
    raw_to = os.environ.get("EMAIL_TO", "").strip()
    if "@" in raw_to:
        user, _, dom = raw_to.partition("@")
        email_to = "{}{}@{}".format(user[:1], "\u2022" * 5, dom)
    else:
        email_to = "not set"

    n_listings = len((latest or {}).get("listings") or [])
    listing_note = ("{} listings seen on the last scan".format(n_listings)
                    if n_listings else "Per-seat detail unavailable")

    repl = {
        "__STATUS_CLASS__": sclass,
        "__STATUS_TEXT__": stext,
        "__LAST_SCAN__": esc(fmt_ts(history[-1]["ts"]) if history else "never"),
        "__SCAN_COUNT__": str(len(history)),
        "__EMAIL__": esc(email_to),
        "__TIER_CARDS__": build_tiers(cfg, latest, state),
        "__CHART_BODY__": chart_body,
        "__LISTING_NOTE__": esc(listing_note),
        "__LISTINGS_TABLE__": build_table(latest),
        "__RANGE_PANEL__": build_range(latest),
        "__SITE_LINKS__": build_sites(),
        "__GENERATED__": esc(datetime.now().strftime("%b %-d, %Y at %-I:%M %p")),
        "__DATA_JSON__": json.dumps(chart_data),
    }
    for k, v in repl.items():
        tpl = tpl.replace(k, v)

    with open(P("dashboard.html"), "w") as fh:
        fh.write(tpl)
    print("Wrote {} ({:,} bytes)".format(P("dashboard.html"), len(tpl)))


if __name__ == "__main__":
    main()

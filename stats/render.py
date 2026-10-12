#!/usr/bin/env python3
"""Render RuneLite Plugin Hub stats for this profile README. Stdlib only.

  python3 stats/render.py --hub-dir /path/to/runelite/plugin-hub --owner 1504681

Discovers plugins by grepping the hub's plugins/ files for the owner's repos, takes live
install counts from api.runelite.net, history from runelite.phyce.dev (hourly since 2025),
and writes stats/overview-{light,dark}.svg, stats/data.json and the table in README.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

UA = {"User-Agent": "github-profile-stats"}
DAYS = 90
THEMES = {
    "light": {"bg": "#fcfcfb", "ink": "#0b0b0b", "muted": "#52514e", "line": "#e4e3df",
              "accent": "#2a78d6", "soft": "#cde2fb", "fill": "0.45", "good": "#0ca30c", "bad": "#d03b3b"},
    "dark": {"bg": "#1a1a19", "ink": "#f0efec", "muted": "#c3c2b7", "line": "#2c2c2a",
             "accent": "#3987e5", "soft": "#1c5cab", "fill": "0.28", "good": "#0ca30c", "bad": "#e66767"},
}
FONT = "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"


def get(url: str, timeout: int = 60) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def get_json(url: str):
    return json.loads(get(url))


def parse_props(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def discover(hub_dir: str, owner: str) -> dict:
    """hub id -> (owner, repo) for every hub plugin built from the owner's GitHub repos."""
    out = {}
    pdir = os.path.join(hub_dir, "plugins")
    for name in sorted(os.listdir(pdir)):
        with open(os.path.join(pdir, name), encoding="utf-8", errors="replace") as f:
            p = parse_props(f.read())
        m = re.match(r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", p.get("repository", ""))
        if m and m.group(1).lower() == owner.lower():
            out[name] = (m.group(1), m.group(2))
    return out


def live_counts() -> dict:
    ver = get_json("https://static.runelite.net/bootstrap.json")["version"]
    return {k: int(v) for k, v in get_json(f"https://api.runelite.net/runelite-{ver}/pluginhub").items()}


def history(pid: str) -> list[tuple[int, int]]:
    try:
        d = get_json(f"https://api-runelite.phyce.dev/plugin/{pid}/history?range=all")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise
    pts = []
    for row in d.get("data", []) if d.get("success") else []:
        ts = int(datetime.strptime(row["date"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
        pts.append((ts, int(row["count"])))
    pts.sort()
    return pts


def meta(owner: str, repo: str) -> dict:
    for branch in ("master", "main"):
        try:
            return parse_props(get(f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/runelite-plugin.properties").decode())
        except urllib.error.HTTPError:
            continue
    return {}


def daily(pts: list[tuple[int, int]], days: list[int]) -> list[int | None]:
    """Count at the end of each day (carry forward); None before the first point."""
    out, i, last = [], 0, None
    for day_end in days:
        while i < len(pts) and pts[i][0] <= day_end:
            last = pts[i][1]
            i += 1
        out.append(last)
    return out


def value_at(pts: list[tuple[int, int]], ts: int, tol: int) -> int | None:
    best = min(pts, key=lambda q: abs(q[0] - ts), default=None)
    return best[1] if best and abs(best[0] - ts) <= tol else None


def fmt(n) -> str:
    return "—" if n is None else f"{n:,}"


def compact(n) -> str:
    if n is None:
        return "—"
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.1f}K" if n >= 10000 else f"{n:,}"


def signed(n) -> str:
    return "—" if n is None else f"{n:+,}"


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---- SVG -------------------------------------------------------------------------------
def spark(points: list[int | None], x: float, y: float, w: float, h: float, t: dict) -> str:
    vals = [(i, v) for i, v in enumerate(points) if v is not None]
    if len(vals) < 2:
        return f'<text x="{x}" y="{y + h - 4}" font-size="10" fill="{t["muted"]}">new</text>'
    lo, hi = min(v for _, v in vals), max(v for _, v in vals)
    if hi == lo:
        hi += 1
        lo -= 1
    n = len(points) - 1
    px = lambda i: x + w * i / n
    py = lambda v: y + h - h * (v - lo) / (hi - lo)
    d = " ".join(f"{'M' if k == 0 else 'L'}{px(i):.1f} {py(v):.1f}" for k, (i, v) in enumerate(vals))
    area = d + f" L{px(vals[-1][0]):.1f} {y + h:.1f} L{px(vals[0][0]):.1f} {y + h:.1f} Z"
    li, lv = vals[-1]
    return (f'<path d="{area}" fill="{t["soft"]}" opacity="{t["fill"]}"/>'
            f'<path d="{d}" fill="none" stroke="{t["accent"]}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="{px(li):.1f}" cy="{py(lv):.1f}" r="2.6" fill="{t["accent"]}" stroke="{t["bg"]}" stroke-width="1.5"/>')


def card(theme: str, data: dict) -> str:
    t = THEMES[theme]
    plugins = data["plugins"]
    W, PAD = 820, 24
    rows_y = 318
    row_h = 30
    H = rows_y + row_h * len(plugins) + 44
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{FONT}" role="img" aria-label="RuneLite Plugin Hub stats">',
         f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="12" fill="{t["bg"]}" stroke="{t["line"]}"/>']
    # header
    o.append(f'<text x="{PAD}" y="36" font-size="18" font-weight="700" fill="{t["ink"]}">RuneLite Plugin Hub</text>')
    o.append(f'<text x="{W - PAD}" y="36" font-size="11" fill="{t["muted"]}" text-anchor="end">{len(plugins)} plugins · updated {data["updated"]}</text>')
    # tiles
    tiles = [("Active installs", compact(data["total"]), ""),
             ("Last 30 days", signed(data["total_d30"]), t["good"] if (data["total_d30"] or 0) > 0 else t["bad"] if (data["total_d30"] or 0) < 0 else t["ink"]),
             ("Best hub rank", f"#{fmt(data['best_rank'])}", ""),
             ("Plugins listed on the hub", fmt(data["hub_total"]), "")]
    tw = (W - 2 * PAD - 12 * 3) / 4
    for i, (label, value, color) in enumerate(tiles):
        x = PAD + i * (tw + 12)
        o.append(f'<rect x="{x}" y="56" width="{tw:.0f}" height="64" rx="8" fill="none" stroke="{t["line"]}"/>')
        o.append(f'<text x="{x + 12}" y="78" font-size="11" fill="{t["muted"]}">{label}</text>')
        o.append(f'<text x="{x + 12}" y="108" font-size="24" font-weight="600" fill="{color or t["ink"]}">{value}</text>')
    # total chart (90 days)
    cx, cy, cw, ch = PAD, 150, W - 2 * PAD - 70, 120
    series = data["total_series"]
    vals = [v for v in series if v is not None]
    o.append(f'<text x="{PAD}" y="142" font-size="11" fill="{t["muted"]}">Active installs across all plugins · last {DAYS} days</text>')
    if len(vals) >= 2:
        lo, hi = min(vals), max(vals)
        span = max(hi - lo, 1)
        lo2, hi2 = lo - span * 0.08, hi + span * 0.08
        n = len(series) - 1
        px = lambda i: cx + cw * i / n
        py = lambda v: cy + ch - ch * (v - lo2) / (hi2 - lo2)
        for g in (0.25, 0.75):
            yv = lo2 + (hi2 - lo2) * g
            o.append(f'<line x1="{cx}" y1="{py(yv):.1f}" x2="{cx + cw}" y2="{py(yv):.1f}" stroke="{t["line"]}" stroke-dasharray="3 3"/>')
            o.append(f'<text x="{cx + cw + 6}" y="{py(yv) + 4:.1f}" font-size="10" fill="{t["muted"]}">{compact(round(yv))}</text>')
        pts = [(i, v) for i, v in enumerate(series) if v is not None]
        d = " ".join(f"{'M' if k == 0 else 'L'}{px(i):.1f} {py(v):.1f}" for k, (i, v) in enumerate(pts))
        o.append(f'<path d="{d} L{px(pts[-1][0]):.1f} {cy + ch} L{px(pts[0][0]):.1f} {cy + ch} Z" fill="{t["soft"]}" opacity="{t["fill"]}"/>')
        o.append(f'<path d="{d}" fill="none" stroke="{t["accent"]}" stroke-width="2" stroke-linejoin="round"/>')
        li, lv = pts[-1]
        o.append(f'<circle cx="{px(li):.1f}" cy="{py(lv):.1f}" r="3.5" fill="{t["accent"]}" stroke="{t["bg"]}" stroke-width="2"/>')
        o.append(f'<text x="{px(li) - 8:.1f}" y="{py(lv) - 8:.1f}" font-size="11" font-weight="600" fill="{t["ink"]}" text-anchor="end">{fmt(lv)}</text>')
        for i, label in data["x_labels"]:
            o.append(f'<text x="{px(i):.1f}" y="{cy + ch + 16}" font-size="10" fill="{t["muted"]}" text-anchor="middle">{label}</text>')
    o.append(f'<line x1="{cx}" y1="{cy + ch}" x2="{cx + cw}" y2="{cy + ch}" stroke="{t["line"]}"/>')
    # per-plugin rows
    hy = rows_y - 10
    for x, label, anchor in ((PAD, "Plugin", "start"), (PAD + 330, "Installs", "end"), (PAD + 352, f"{DAYS} days", "start"), (W - PAD, "30-day change", "end")):
        o.append(f'<text x="{x}" y="{hy}" font-size="10" fill="{t["muted"]}" text-anchor="{anchor}" letter-spacing="0.5">{label.upper()}</text>')
    o.append(f'<line x1="{PAD}" y1="{hy + 6}" x2="{W - PAD}" y2="{hy + 6}" stroke="{t["line"]}"/>')
    for i, p in enumerate(plugins):
        y = rows_y + i * row_h
        o.append(f'<text x="{PAD}" y="{y + 14}" font-size="13" font-weight="600" fill="{t["ink"]}">{esc(p["name"])}</text>')
        o.append(f'<text x="{PAD + 330}" y="{y + 14}" font-size="13" fill="{t["ink"]}" text-anchor="end" font-variant-numeric="tabular-nums">{fmt(p["installs"])}</text>')
        o.append(spark(p["series"], PAD + 352, y - 2, 300, 22, t))
        d = p["d30"]
        col = t["good"] if (d or 0) > 0 else t["bad"] if (d or 0) < 0 else t["muted"]
        o.append(f'<text x="{W - PAD}" y="{y + 14}" font-size="12" fill="{col}" text-anchor="end" font-variant-numeric="tabular-nums">{signed(d)}</text>')
        o.append(f'<line x1="{PAD}" y1="{y + row_h - 6}" x2="{W - PAD}" y2="{y + row_h - 6}" stroke="{t["line"]}" opacity="0.6"/>')
    o.append(f'<text x="{PAD}" y="{H - 14}" font-size="10" fill="{t["muted"]}">Active installs as reported by the RuneLite client · counts api.runelite.net · history runelite.phyce.dev</text>')
    o.append("</svg>")
    return "\n".join(o)


# ---- main -------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub-dir", required=True)
    ap.add_argument("--owner", required=True)
    ap.add_argument("--out", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--readme", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "README.md"))
    a = ap.parse_args()

    now = datetime.now(timezone.utc)
    now_ts = int(now.timestamp())
    day_ends = [int((now - timedelta(days=DAYS - 1 - i)).replace(hour=23, minute=59, second=59).timestamp()) for i in range(DAYS)]
    day_ends[-1] = now_ts
    counts = live_counts()
    ranking = sorted(counts.values(), reverse=True)
    plugins = []
    for pid, (owner, repo) in discover(a.hub_dir, a.owner).items():
        pts = history(pid)
        m = meta(owner, repo)
        installs = counts.get(pid)
        if installs is not None:
            pts.append((now_ts, installs))
        series = daily(pts, day_ends)
        d30 = None
        if installs is not None:
            v = value_at(pts, now_ts - 30 * 86400, 2 * 86400)
            if v is None and len(pts) > 1 and pts[0][0] > now_ts - 30 * 86400:
                v = 0  # listed within the last 30 days: the whole count is new
            d30 = None if v is None else installs - v
        plugins.append({
            "id": pid, "name": m.get("displayName", pid), "description": m.get("description", ""),
            "repo": f"https://github.com/{owner}/{repo}", "hub": f"https://runelite.net/plugin-hub/show/{pid}",
            "installs": installs, "rank": (ranking.index(installs) + 1) if installs is not None else None,
            "d30": d30, "series": series,
        })
    plugins.sort(key=lambda p: -(p["installs"] or 0))

    total_series = []
    for i in range(DAYS):
        vals = [p["series"][i] for p in plugins if p["series"][i] is not None]
        total_series.append(sum(vals) if vals else None)
    total = sum(p["installs"] or 0 for p in plugins)
    total_d30 = sum(p["d30"] for p in plugins if p["d30"] is not None) if any(p["d30"] is not None for p in plugins) else None
    x_labels = [(i, datetime.fromtimestamp(day_ends[i], timezone.utc).strftime("%-d %b")) for i in (0, DAYS // 3, 2 * DAYS // 3, DAYS - 1)]
    data = {
        "updated": now.strftime("%-d %b %Y %H:%M UTC"), "total": total, "total_d30": total_d30,
        "best_rank": min((p["rank"] for p in plugins if p["rank"]), default=None), "hub_total": len(counts),
        "total_series": total_series, "x_labels": x_labels, "plugins": plugins,
    }
    os.makedirs(a.out, exist_ok=True)
    for theme in THEMES:
        with open(os.path.join(a.out, f"overview-{theme}.svg"), "w", encoding="utf-8") as f:
            f.write(card(theme, data))
    with open(os.path.join(a.out, "data.json"), "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in data.items() if k not in ("total_series", "x_labels")} | {"plugins": [{k: v for k, v in p.items() if k != "series"} for p in plugins]}, f, indent=1)

    # README table between markers (the accessible, linkable twin of the card)
    rows = ["| Plugin | What it does | Installs | 30 days |", "|---|---|---:|---:|"]
    for p in plugins:
        rows.append(f"| [{p['name']}]({p['hub']}) ([source]({p['repo']})) | {p['description']} | {fmt(p['installs'])} | {signed(p['d30'])} |")
    table = "\n".join(rows)
    try:
        with open(a.readme, encoding="utf-8") as f:
            readme = f.read()
    except FileNotFoundError:
        readme = ""
    start, end = "<!-- plugins:start -->", "<!-- plugins:end -->"
    if start in readme and end in readme:
        i, j = readme.index(start) + len(start), readme.index(end)
        readme = readme[:i] + "\n" + table + "\n" + readme[j:]
        with open(a.readme, "w", encoding="utf-8") as f:
            f.write(readme)
    print(f"{len(plugins)} plugins, {total:,} installs, 30d {signed(total_d30)}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate a cross-table HTML page for a PlayHQ grade."""

import argparse
import json
import os
import re
import sys
from datetime import date as Date, datetime, timedelta
from playhq_api import fetch_grade_fixtures, rounds_to_fixtures

TEAM_META = {
    "Baulkham Hills Shire 15U": ("BHS", "#1565c0"),
    "Bankstown City 15U":       ("BAN", "#c2185b"),
    "Blacktown City 15U":       ("BCY", "#bf360c"),
    "Camden & District 15U":   ("CAM", "#2e7d32"),
    "Charlestown 15U":          ("CHA", "#6a1b9a"),
    "Eastwood Ryde 15U":        ("ERY", "#00695c"),
    "Gosford 15U":              ("GOS", "#e65100"),
    "Hastings Valley 15U":      ("HAS", "#283593"),
    "Hills District 15U":       ("HDS", "#00838f"),
    "Illawarra District 15U":   ("ILL", "#558b2f"),
    "Ku-ring-gai 15U":          ("KNA", "#4527a0"),
    "Maitland 15U":             ("MAI", "#f57f17"),
    "Manly Warringah 15U":      ("MAN", "#880e4f"),
    "Newcastle 15U":            ("NEW", "#1b5e20"),
    "Northern Suburbs 15U":     ("NSB", "#0d47a1"),
    "Penrith District 15U":     ("PEN", "#5d4037"),
    "Randwick 15U":             ("RAN", "#c62828"),
    "Sutherland Shire 15U":     ("SUT", "#006064"),
    "Wagga Wagga 15U":          ("WAG", "#37474f"),
    "Wyong District 15U":       ("WYO", "#4e342e"),
    "Campbelltown District 15U": ("CDN", "#6d4c41"),
    "Hawkesbury City 15U":      ("HAW", "#0097a7"),
    "Lakeside 15U":             ("LAK", "#689f38"),
    "Liverpool City 15U":       ("LIV", "#d84315"),
    "St George District 15U":   ("STG", "#f9a825"),
}

COLOR_PALETTE = [
    "#1565c0","#c2185b","#bf360c","#2e7d32","#6a1b9a",
    "#00695c","#e65100","#283593","#00838f","#558b2f",
    "#4527a0","#f57f17","#880e4f","#1b5e20","#0d47a1",
    "#5d4037","#c62828","#006064","#37474f","#4e342e",
]


def _parse_round_n(name):
    m = re.search(r'\d+', name or "")
    return int(m.group()) if m else 0


def _parse_court_n(court_str):
    m = re.search(r'\d+', court_str or "")
    return int(m.group()) if m else 0


def _fmt_time(t):
    return (t or "")[:5]


def _fmt_day(date_str):
    try:
        return Date.fromisoformat(date_str).strftime("%a")
    except Exception:
        return ""


def _fmt_sdate(date_str):
    try:
        d = Date.fromisoformat(date_str)
        return f"{d.day}/{d.month}"
    except Exception:
        return ""


def _date_range_str(dates):
    if not dates:
        return ""
    s = Date.fromisoformat(min(dates))
    e = Date.fromisoformat(max(dates))
    if s == e:
        return s.strftime("%-d %b %Y")
    if s.month == e.month and s.year == e.year:
        return f"{s.strftime('%a %-d')}–{e.strftime('%a %-d %b %Y')}"
    return f"{s.strftime('%a %-d %b')}–{e.strftime('%a %-d %b %Y')}"


def _short_code(name):
    name = re.sub(r'\s+\d+U\s*$', '', name).strip()
    words = name.split()
    if len(words) == 1:
        return words[0][:3].upper()
    return ''.join(w[0] for w in words[:4]).upper()


def _display_name(name):
    """Strip age-group suffix (e.g. '15U') from a team name for display."""
    return re.sub(r'\s+\d+U\s*$', '', name).strip()


def is_grade_active(fixtures):
    """Return True if the grade is currently within its playing window for today.

    Active = current time is between the first round's start and 30 minutes
    after the last round's start, for rounds scheduled today.
    """
    now = datetime.now()
    today = now.date().isoformat()

    times = []
    for f in fixtures:
        if f.date == today and f.time:
            try:
                t = datetime.strptime(f.time[:5], "%H:%M").replace(
                    year=now.year, month=now.month, day=now.day)
                times.append(t)
            except ValueError:
                pass

    if not times:
        return False

    return min(times) <= now <= max(times) + timedelta(minutes=30)


def _slugify(title):
    """Convert a title to a filename-safe slug, e.g. '15U Championship 2026' → '15u-championship-2026.html'."""
    slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
    return f"{slug}.html"


def build_data(fixtures, team_meta=None, title="Cross Table", pin=None,
               max_round=None, next_url=None):
    """Return the DATA dict for the HTML template.

    max_round: if set, treat any game in a later round as 'upcoming' and
               exclude those results from standings.
    """
    meta = team_meta or TEAM_META

    teams_seen = set()
    for f in fixtures:
        if f.home_team:
            teams_seen.add(f.home_team)
        if f.away_team and f.away_team != "BYE":
            teams_seen.add(f.away_team)

    code_map = {}
    color_map = {}
    for i, name in enumerate(sorted(teams_seen)):
        if name in meta:
            code, color = meta[name]
        else:
            code = _short_code(name)
            color = COLOR_PALETTE[i % len(COLOR_PALETTE)]
        code_map[name] = code
        color_map[name] = color

    stats = {t: {"W": 0, "L": 0, "D": 0, "PF": 0, "PA": 0} for t in teams_seen}
    for f in fixtures:
        if f.away_team == "BYE" or f.status not in ("completed", "live") or f.home_score is None:
            continue
        rn = _parse_round_n(f.round_name)
        if max_round is not None and rn > max_round:
            continue
        h, a, hs, as_ = f.home_team, f.away_team, f.home_score, f.away_score
        stats[h]["PF"] += hs; stats[h]["PA"] += as_
        stats[a]["PF"] += as_; stats[a]["PA"] += hs
        if hs > as_:
            stats[h]["W"] += 1; stats[a]["L"] += 1
        elif hs < as_:
            stats[a]["W"] += 1; stats[h]["L"] += 1
        else:
            stats[h]["D"] += 1; stats[a]["D"] += 1

    has_results = any(s["W"] + s["L"] + s["D"] > 0 for s in stats.values())

    def sort_key(t):
        s = stats[t]
        pts = s["W"] * 2 + s["D"]
        pct = s["PF"] / s["PA"] if s["PA"] > 0 else 0
        return (-pts, -pct)

    ordered = sorted(teams_seen, key=sort_key if has_results else None)

    teams_js = []
    for i, name in enumerate(ordered, 1):
        s = stats[name]
        pct = (s["PF"] / s["PA"] * 100) if s["PA"] > 0 else 0
        teams_js.append({
            "code": code_map[name],
            "name": _display_name(name),
            "rank": i,
            "pts": s["W"] * 2 + s["D"],
            "pct": f"{pct:.1f}",
            "W": s["W"],
            "L": s["L"],
            "D": s["D"],
        })

    round_meta = {}
    for f in fixtures:
        rn = _parse_round_n(f.round_name)
        if rn and rn not in round_meta and f.date:
            st = "done" if f.status == "completed" else ("live" if f.status == "live" else "upcoming")
            if max_round is not None and rn > max_round:
                st = "upcoming"
            round_meta[rn] = {"n": rn, "day": _fmt_day(f.date),
                              "time": _fmt_time(f.time), "sdate": _fmt_sdate(f.date), "state": st}

    rounds_js = [round_meta[rn] for rn in sorted(round_meta)]

    games_js = []
    for f in fixtures:
        if f.away_team == "BYE":
            continue
        rn = _parse_round_n(f.round_name)
        hc = code_map.get(f.home_team, f.home_team[:3])
        ac = code_map.get(f.away_team, f.away_team[:3])
        if max_round is not None and rn > max_round:
            st = "upcoming"
            hs, as_ = None, None
        else:
            st = "done" if f.status == "completed" else ("live" if f.status == "live" else "upcoming")
            hs, as_ = f.home_score, f.away_score
        games_js.append([rn, hc, hs, ac, as_, _parse_court_n(f.court), st])

    venue = next((f.venue for f in fixtures if f.venue), "")
    dates = sorted(set(f.date for f in fixtures if f.date))

    return {
        "title": title,
        "venue": venue,
        "date": _date_range_str(dates),
        "pin": pin,
        "next_url": next_url,
        "teams": teams_js,
        "rounds": rounds_js,
        "games": games_js,
    }


CSS = """\
  :root {
    --ink:      #15223b;
    --ink-2:    #51607a;
    --muted:    #9aa4b6;
    --line:     #dfe3ea;
    --line-2:   #eef0f4;
    --bg:       #f3f5f8;
    --card:     #ffffff;
    --accent:   #e8542f;
    --accent-bg:#fdece5;
    --win:      #157a47;
    --win-bg:   #e7f3ea;
    --loss:     #b23b2c;
    --loss-bg:  #fbe9e6;
    --draw-bg:  #f0f1f4;
    --col-w:    52px;
    --name-w:   110px;
    --row-h:    33px;
  }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  html, body { margin: 0; height: 100%; }
  body {
    font-family: 'Barlow', system-ui, -apple-system, sans-serif;
    background: var(--bg);
    color: var(--ink);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .scroll {
    flex: 1 1 auto;
    overflow: auto;
    -webkit-overflow-scrolling: touch;
    padding-bottom: 24px;
  }
  table.x { border-collapse: separate; border-spacing: 0; margin: 0; }
  table.x th, table.x td { padding: 0; margin: 0; }
  th.corner {
    position: sticky; left: 0; top: 0; z-index: 6;
    width: var(--name-w); min-width: var(--name-w);
    background: var(--card);
    border-right: 2px solid var(--line);
    border-bottom: 2px solid var(--line);
  }
  th.corner .lbl {
    font-family: 'Barlow Semi Condensed', sans-serif;
    font-size: 11px; font-weight: 700;
    color: var(--accent);
    padding: 5px 6px; text-align: left; line-height: 1.3;
  }
  th.corner .lbl a { color: inherit; text-decoration: none; }
  th.corner .lbl a:hover { text-decoration: underline; }
  th.colhead {
    position: sticky; top: 0; z-index: 4;
    width: var(--col-w); min-width: var(--col-w);
    background: var(--card);
    border-bottom: 2px solid var(--line);
    border-left: 1px solid var(--line-2);
    vertical-align: bottom;
    padding: 4px 3px;
    font-family: 'Barlow Semi Condensed', sans-serif;
    font-weight: 700; font-size: 10px;
    color: var(--ink); text-align: center;
    overflow-wrap: break-word; word-break: break-word;
    line-height: 1.15; vertical-align: top;
  }
  th.colhead .ckn {
    display: block;
    font-size: 8px; font-weight: 700;
    color: var(--muted); letter-spacing: .3px;
    line-height: 1.4;
  }
  th.rowhead {
    position: sticky; left: 0; z-index: 3;
    width: var(--name-w); min-width: var(--name-w);
    height: var(--row-h);
    background: var(--card);
    border-right: 2px solid var(--line);
    border-bottom: 1px solid var(--line-2);
    text-align: left; vertical-align: top;
  }
  th.rowhead .rn {
    display: flex; align-items: center; gap: 4px;
    padding: 3px 5px 3px 5px;
  }
  th.rowhead .rkn {
    flex: 0 0 auto; width: 13px;
    font-family: 'Barlow Semi Condensed', sans-serif;
    font-size: 11px; font-weight: 700;
    color: var(--muted); text-align: center;
  }
  th.rowhead .nm {
    font-size: 10px; font-weight: 600;
    line-height: 1.1; color: var(--ink);
  }
  td.cell {
    width: var(--col-w); min-width: var(--col-w);
    height: var(--row-h);
    border-left: 1px solid var(--line-2);
    border-bottom: 1px solid var(--line-2);
    position: relative;
    background: var(--card);
    vertical-align: top;
  }
  td.cell .scblock {
    position: absolute; inset: 0;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    gap: 1px; padding: 2px;
  }
  td.cell .scblock .rd {
    font-size: 8px; font-weight: 700;
    letter-spacing: .3px; color: var(--muted); line-height: 1;
  }
  td.cell .scblock .score {
    font-family: 'Barlow Semi Condensed', sans-serif;
    font-weight: 700; font-size: 13px;
    line-height: 1; letter-spacing: -.1px;
  }
  td.cell.fix { background: #f9fafb; }
  td.cell.fix .fxblock {
    position: absolute; inset: 0;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    gap: 1px; padding: 2px;
    text-align: center; line-height: 1.1;
  }
  td.cell.fix .fl1 {
    font-size: 9.5px; font-weight: 600;
    color: var(--ink-2); white-space: nowrap;
  }
  td.cell.fix .fl1 b { color: var(--ink); font-weight: 700; }
  td.cell.fix .fl2 {
    font-size: 8.5px; font-weight: 500;
    color: var(--muted); white-space: nowrap;
  }
  td.cell.fix.done { background: #f5f6f8; }
  td.cell.fix.done .fl1 { color: var(--muted); }
  td.cell.fix.done .fl1 b { color: var(--ink-2); }
  td.cell.fix.done .fl2 { color: #bcc4d0; }
  td.cell.fix.live { background: var(--accent-bg); box-shadow: inset 0 0 0 2px var(--accent); }
  td.cell.fix.live .fl1, td.cell.fix.live .fl1 b { color: var(--accent); }
  td.cell.fix.live .fl2 { color: var(--accent); }
  td.cell.win  { background: var(--win-bg); }
  td.cell.win  .score { color: var(--win); }
  td.cell.loss { background: var(--loss-bg); }
  td.cell.loss .score { color: var(--loss); }
  td.cell.draw { background: var(--draw-bg); }
  td.cell.draw .score { color: var(--ink-2); }
  td.cell.live {
    background: var(--accent-bg);
    box-shadow: inset 0 0 0 2px var(--accent);
  }
  td.cell.live .score {
    color: var(--accent);
    display: flex; align-items: center; justify-content: center; gap: 3px;
  }
  td.cell.live .dot {
    width: 5px; height: 5px; border-radius: 50%;
    background: var(--accent); display: inline-block;
    animation: pulse 1.3s infinite;
  }
  td.cell.upcoming .score { color: var(--muted); font-size: 11px; }
  td.cell.next:not(.live) { box-shadow: inset 0 0 0 2px #5c6bc0; }
  td.cell.next.fix:not(.live) { background: #eef0fb; }
  @keyframes pulse { 0%,100%{opacity:1;transform:scale(1);} 50%{opacity:.3;transform:scale(.6);} }
  td.cell.self {
    background:
      repeating-linear-gradient(-45deg, transparent 0 5px, rgba(255,255,255,.04) 5px 6px),
      var(--ink);
    overflow: hidden;
  }
  td.cell.self .selfblock {
    position: absolute; inset: 0;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    gap: 1px; padding: 2px;
    font-family: 'Barlow Semi Condensed', sans-serif;
    color: #fff; text-align: center; line-height: 1.1;
  }
  td.cell.self .sl1 { font-family: 'Barlow Semi Condensed', sans-serif; font-size: 13px; font-weight: 700; white-space: nowrap; line-height: 1; }
  td.cell.self .sl2 { font-size: 8px; font-weight: 700; letter-spacing: .3px; color: #aeb9cc; white-space: nowrap; line-height: 1; }
  tr.pinned th.rowhead { background: #fff7ec; }
  tr.pinned td.cell:not(.self) { background-color: #fffaf2; }
  tr.pinned td.cell.win  { background-color: var(--win-bg); }
  tr.pinned td.cell.loss { background-color: var(--loss-bg); }
  tr.pinned td.cell.live { background-color: var(--accent-bg); }
  td.cell.pincol:not(.self):not(.win):not(.loss):not(.live) { background-color: #fffaf2; }
  @media (max-width: 400px) {
    :root { --col-w: 42px; --name-w: 90px; }
  }
"""

JS_RENDER = """\
function render(d) {
  const key = (a, b) => [a, b].sort().join("|");
  const M = {};
  d.games.forEach(([rd, h, hs, a, as_, court, st]) => {
    const k = key(h, a);
    const prev = M[k];
    // Prefer completed result over upcoming; break ties by earlier round.
    if (!prev || (prev.st !== "done" && st === "done") || (prev.st === st && rd < prev.rd))
      M[k] = { rd, h, hs, a, as: as_, court, st };
  });
  const roundByN = Object.fromEntries(d.rounds.map(r => [r.n, r]));

  // Per-team next round: min round where their game isn't done
  const nextRound = {};
  d.teams.forEach(t => {
    let min = Infinity;
    d.games.forEach(([rd,, , , , , st]) => { if (st !== "done" && rd < min) min = rd; });
    // narrow to this team's games
    min = Infinity;
    d.games.forEach(([rd, h, , a, , , st]) => {
      if ((h === t.code || a === t.code) && st !== "done" && rd < min) min = rd;
    });
    nextRound[t.code] = min === Infinity ? null : min;
  });

  const grid = document.getElementById("grid");
  let html = "<thead><tr>";
  const titleEl = d.next_url
    ? `<a href="${d.next_url}">${d.title}</a>`
    : d.title;
  html += `<th class="corner"><div class="lbl">${titleEl}</div></th>`;
  d.teams.forEach(t => {
    html += `<th class="colhead${t.code === d.pin ? " pinhead" : ""}"><span class="ckn">${t.rank}</span>${t.name}</th>`;
  });
  html += "</tr></thead><tbody>";

  d.teams.forEach((rowT, ri) => {
    const pinnedRow = rowT.code === d.pin;
    html += `<tr class="${pinnedRow ? "pinned" : ""}">`;
    html += `<th class="rowhead">
               <div class="rn">
                 <span class="rkn">${rowT.rank}</span>
                 <span class="nm">${rowT.name}</span>
               </div>
             </th>`;

    d.teams.forEach((colT, ci) => {
      const pinCol = colT.code === d.pin && !pinnedRow;
      const pcls = pinCol ? " pincol" : "";

      if (rowT.code === colT.code) {
        html += `<td class="cell self">
                   <div class="selfblock">
                     <span class="sl1">${rowT.pts} pts</span>
                     <span class="sl2">${rowT.pct}%</span>
                   </div>
                 </td>`;
        return;
      }

      const m = M[key(rowT.code, colT.code)];
      if (!m) { html += `<td class="cell${pcls}"></td>`; return; }
      const rmeta = roundByN[m.rd] || {};
      const rowScore = m.h === rowT.code ? m.hs : m.as;
      const colScore = m.h === rowT.code ? m.as : m.hs;
      const ncls = m.rd === nextRound[rowT.code] ? " next" : "";

      if (m.st === "live" && rowScore != null) {
        html += `<td class="cell live${ncls}${pcls}">
                   <div class="scblock">
                     <span class="rd">R${m.rd}</span>
                     <span class="score"><span class="dot"></span>${rowScore}–${colScore}</span>
                   </div>
                 </td>`;
      } else if (m.st === "done" && rowScore != null) {
        const cls = rowScore > colScore ? "win" : rowScore < colScore ? "loss" : "draw";
        html += `<td class="cell ${cls}${ncls}${pcls}">
                   <div class="scblock">
                     <span class="rd">R${m.rd}</span>
                     <span class="score">${rowScore}–${colScore}</span>
                   </div>
                 </td>`;
      } else {
        const fixCls = m.st === "live" ? " live" : "";
        html += `<td class="cell fix${fixCls}${ncls}${pcls}">
                   <div class="fxblock">
                     <span class="fl1"><b>R${m.rd}</b> C${m.court}</span>
                     <span class="fl2">${rmeta.day || ""} ${rmeta.time || ""}</span>
                   </div>
                 </td>`;
      }
    });
    html += "</tr>";
  });

  html += "</tbody>";
  grid.innerHTML = html;
}
render(DATA);
"""

HTML_TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1" />
<title>{page_title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Semi+Condensed:wght@500;600;700&family=Barlow:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
{css}
</style>
</head>
<body>
  <div class="scroll">
    <table class="x" id="grid"></table>
  </div>
<script>
const DATA = {data_json};

{js_render}
</script>
</body>
</html>
"""


def generate_html(data: dict) -> str:
    page_title = f"{data['title']} — Cross Table"
    data_json = json.dumps(data, indent=2, ensure_ascii=False)
    return HTML_TEMPLATE.format(
        page_title=page_title,
        css=CSS,
        data_json=data_json,
        js_render=JS_RENDER,
    )


def _generate_one(grade_id, title, output, pin=None, max_round=None, next_url=None,
                  force_regen=False):
    print(f"Fetching fixtures for grade {grade_id} ({title})...", file=sys.stderr)
    rounds_data = fetch_grade_fixtures(grade_id)
    fixtures = rounds_to_fixtures(rounds_data)
    print(f"  {len(fixtures)} fixtures across {len(rounds_data)} rounds", file=sys.stderr)

    if not force_regen and output != "-" and os.path.exists(output):
        if not is_grade_active(fixtures):
            print(f"  Skipping — not active today (--force-regen to override)", file=sys.stderr)
            return

    data = build_data(fixtures, title=title, pin=pin,
                      max_round=max_round, next_url=next_url)
    html = generate_html(data)

    if output == "-":
        print(html)
    else:
        tmp = output + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(html)
        os.replace(tmp, output)
        print(f"  Written to {output}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Generate cross-table HTML pages from PlayHQ data")
    parser.add_argument("--config", "-c", help="JSON config file with a list of grades to generate")
    parser.add_argument("--grade-id", help="PlayHQ grade ID (8-char hex)")
    parser.add_argument("--title", default="Cross Table", help="Page title")
    parser.add_argument("--pin", default=None, help="Team code to highlight (or omit)")
    parser.add_argument("--rounds", type=int, default=None,
                        help="Show scores only up to this round; later rounds show fixture info")
    parser.add_argument("--output", "-o", default="-", help="Output file path (- for stdout)")
    parser.add_argument("--force-regen", action="store_true",
                        help="Regenerate even if the grade is not currently active")
    args = parser.parse_args()

    if args.config:
        with open(args.config) as f:
            cfg = json.load(f)
        grades = cfg["grades"]
        outputs = [g.get("output") or _slugify(g.get("title", "cross-table")) for g in grades]
        for i, g in enumerate(grades):
            next_url = outputs[(i + 1) % len(grades)]
            _generate_one(
                grade_id=g["grade_id"],
                title=g.get("title", "Cross Table"),
                output=outputs[i],
                pin=g.get("pin"),
                max_round=g.get("rounds"),
                next_url=next_url if len(grades) > 1 else None,
                force_regen=args.force_regen,
            )
    elif args.grade_id:
        _generate_one(
            grade_id=args.grade_id,
            title=args.title,
            output=args.output,
            pin=args.pin,
            max_round=args.rounds,
            force_regen=args.force_regen,
        )
    else:
        parser.error("Provide --config or --grade-id")


if __name__ == "__main__":
    main()

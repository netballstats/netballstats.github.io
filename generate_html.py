#!/usr/bin/env python3
"""Generate a fixture matrix HTML page from scraped PlayHQ data."""

import argparse
import json
import re
from html import escape

PALETTE = [
    "#e65100", "#1565c0", "#2e7d32", "#6a1b9a", "#00838f",
    "#546e7a", "#ad1457", "#ef6c00", "#283593", "#c62828",
    "#00695c", "#4e342e", "#1a237e", "#bf360c",
]

ABBREVIATIONS = [
    ("Northern", "Nth"),
    ("Southern", "Sth"),
    ("Eastern", "Est"),
    ("Western", "Wst"),
    ("Suburbs", "Subs"),
    ("Development", "Dev"),
    ("Association", "Assoc"),
    ("District", "Dist"),
    ("Representative", "Rep"),
]


def shorten_name(name: str, max_len: int = 22) -> str:
    short = name
    for long, abbr in ABBREVIATIONS:
        if len(short) <= max_len:
            break
        short = short.replace(long, abbr)
    return short


def assign_colors(teams: list[str], overrides: dict) -> dict[str, str]:
    colors = {}
    palette_idx = 0
    for t in teams:
        ov = overrides.get(t, {})
        if "color" in ov:
            colors[t] = ov["color"]
        else:
            colors[t] = PALETTE[palette_idx % len(PALETTE)]
            palette_idx += 1
    return colors


def extract_round_number(round_name: str) -> int:
    m = re.search(r"\d+", round_name)
    return int(m.group()) if m else 999


def build_subtitle(data: dict) -> str:
    parts = []
    if data.get("organisation"):
        parts.append(data["organisation"])
    if data.get("name"):
        parts.append(data["name"])

    # Extract venue from first fixture
    fixtures = data.get("grades", [{}])[0].get("fixtures", [])
    venues = {f["venue"] for f in fixtures if f.get("venue")}
    if venues:
        parts.append(next(iter(venues)) if len(venues) == 1 else ", ".join(sorted(venues)))

    # Extract date from first fixture
    dates = sorted({f["date"] for f in fixtures if f.get("date")})
    if dates:
        parts.append(dates[0] if len(dates) == 1 else f"{dates[0]} — {dates[-1]}")

    return " · ".join(parts)


def generate_html(data: dict, config: dict) -> str:
    grades = data.get("grades", [])
    if not grades or not grades[0].get("fixtures"):
        return "<html><body><h1>No fixtures found</h1></body></html>"

    fixtures = grades[0]["fixtures"]
    overrides = config.get("team_overrides", {})

    # Extract teams
    teams = sorted({f["home_team"] for f in fixtures} | {f["away_team"] for f in fixtures})
    teams = [t for t in teams if t != "BYE"]

    # Short names and colors
    short_names = {}
    for t in teams:
        ov = overrides.get(t, {})
        short_names[t] = ov.get("short_name", shorten_name(t))

    colors = assign_colors(teams, overrides)

    # Title
    grade_name = grades[0].get("name", "")
    if grade_name == "(direct)":
        grade_name = data.get("season", "")
    title = config.get("title") or f"{grade_name} — {data.get('name', 'Fixtures')}"
    subtitle = build_subtitle(data)

    # Build TEAMS JS object
    teams_js_entries = []
    for t in teams:
        teams_js_entries.append(
            f'  {json.dumps(short_names[t])}: {{ color: {json.dumps(colors[t])}, full: {json.dumps(t)} }}'
        )
    teams_js = "{\n" + ",\n".join(teams_js_entries) + "\n}"

    # Build MATCHES JS array
    rounds = sorted({f["round_name"] for f in fixtures}, key=extract_round_number)
    total_rounds = len(rounds)
    round_map = {r: extract_round_number(r) for r in rounds}

    matches_js_entries = []
    for f in fixtures:
        rd = round_map.get(f["round_name"], 0)
        time = f.get("time", "")
        court = f.get("court", "")
        court = re.sub(r"^Court\s*", "Ct ", court)
        court = re.sub(r"^Indoor\s*-\s*", "In ", court)
        home = short_names.get(f["home_team"], f["home_team"])
        away = short_names.get(f["away_team"], f["away_team"])
        hs = f["home_score"]
        as_ = f["away_score"]
        hs_js = str(hs) if hs is not None else "null"
        as_js = str(as_) if as_ is not None else "null"
        matches_js_entries.append(
            f'  [{rd},{json.dumps(time)},{json.dumps(court)},{json.dumps(home)},{json.dumps(away)},{hs_js},{as_js}]'
        )
    matches_js = "[\n" + ",\n".join(matches_js_entries) + "\n]"

    # Focus team (shown first, opponents ordered by round)
    focus_team = config.get("focus_team", "")
    # Map to short name if it matches a full name
    focus_short = short_names.get(focus_team, focus_team) if focus_team else ""

    # Generate timestamp in AEDT
    from datetime import datetime, timezone, timedelta
    aedt = timezone(timedelta(hours=11))
    now_aedt = datetime.now(aedt).strftime("%a %d %b %Y, %I:%M %p AEDT")

    return HTML_TEMPLATE.format(
        title=escape(title),
        subtitle=escape(subtitle),
        teams_js=teams_js,
        matches_js=matches_js,
        total_rounds=total_rounds,
        focus_team_js=json.dumps(focus_short),
        updated_time=now_aedt,
    )


HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f8f9fa; padding: 20px; }}
  h1 {{ font-size: 1.3rem; color: #1a1a2e; margin-bottom: 4px; }}
  .subtitle {{ font-size: 0.85rem; color: #555; margin-bottom: 16px; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; align-items: center; }}
  .legend-item {{ display: flex; align-items: center; gap: 5px; font-size: 0.75rem; color: #444; }}
  .legend-swatch {{ width: 14px; height: 14px; border-radius: 3px; border: 2px solid #ccc; }}
  .scroll-wrapper {{ overflow-x: auto; border: 1px solid #d0d0d0; border-radius: 8px; background: #fff; }}
  table {{ border-collapse: collapse; font-size: 0.72rem; min-width: 100%; }}
  th, td {{ border: 1px solid #ddd; padding: 5px 6px; text-align: center; white-space: nowrap; min-width: 110px; }}
  th {{ background: #f0f0f4; font-weight: 700; }}
  thead th {{ position: sticky; top: 0; z-index: 2; }}
  .sticky-col {{ position: sticky; left: 0; z-index: 3; background: #f0f0f4; min-width: 140px; text-align: left; }}
  thead .sticky-col {{ z-index: 4; }}
  td.diagonal {{ background: #e0e0e0; }}
  td.empty-cell {{ background: #fafafa; }}
  .cell-round {{ font-weight: 700; font-size: 0.78rem; }}
  .cell-score {{ font-size: 0.82rem; font-weight: 700; margin-top: 2px; }}
  .cell-score .win {{ color: #2e7d32; }}
  .cell-score .loss {{ color: #c62828; }}
  .cell-score .draw {{ color: #555; }}
  .cell-score .upcoming {{ color: #888; }}
  .cell-time {{ font-size: 0.68rem; color: #555; margin-top: 1px; }}
  .cell-court {{ font-size: 0.66rem; color: #777; margin-top: 1px; }}
  .note {{ font-size: 0.7rem; color: #888; margin-top: 10px; line-height: 1.5; }}
  h2 {{ font-size: 1.1rem; color: #1a1a2e; margin-top: 24px; margin-bottom: 8px; }}
  .standings {{ margin-top: 8px; border-collapse: collapse; font-size: 0.8rem; }}
  .standings th, .standings td {{ border: 1px solid #ddd; padding: 5px 10px; text-align: center; }}
  .standings th {{ background: #f0f0f4; }}
  .standings td:nth-child(2) {{ text-align: left; font-weight: 600; }}
  .standings tr:nth-child(even) {{ background: #fafafa; }}
  .highlight-row td, .highlight-row .sticky-col {{ background-color: #dce8f7 !important; }}
  .highlight-row td.diagonal {{ background-color: #b8cde6 !important; }}
  .highlight-col {{ background-color: #dce8f7 !important; }}
  .highlight-row .highlight-col {{ background-color: #c8d8ee !important; }}
  .bye-list {{ font-size: 0.78rem; color: #555; margin-top: 12px; line-height: 1.8; }}
  .bye-list span {{ display: inline-block; background: #f0f0f4; border: 1px solid #ddd; border-radius: 4px; padding: 1px 8px; margin: 0 2px; font-weight: 600; }}
  @media (max-width: 600px) {{
    body {{ padding: 10px; }}
    th, td {{ padding: 4px 5px; font-size: 0.65rem; min-width: 90px; }}
    .sticky-col {{ min-width: 110px; }}
  }}
</style>
</head>
<body>
<h1>{title}</h1>
<p class="subtitle">{subtitle}</p>
<div class="legend" id="legend"></div>
<div class="scroll-wrapper">
  <table id="matrix"></table>
</div>
<p class="note" id="note"></p>
<div class="bye-list" id="byes"></div>
<div id="standings-section" style="display:none">
  <h2>Standings</h2>
  <table class="standings" id="standings"></table>
</div>
<p class="note" id="updated" style="margin-top:16px"></p>

<script>
const TEAMS = {teams_js};
const MATCHES = {matches_js};
const TOTAL_ROUNDS = {total_rounds};
const FOCUS = {focus_team_js};

const teamNames = Object.keys(TEAMS);

// Build match map
const matchMap = {{}};
teamNames.forEach(t => matchMap[t] = {{}});

MATCHES.forEach(([rd, time, court, home, away, hScore, aScore]) => {{
  matchMap[home][away] = {{ round: rd, time, court, myScore: hScore, theirScore: aScore }};
  matchMap[away][home] = {{ round: rd, time, court, myScore: aScore, theirScore: hScore }};
}});

// Byes
const byes = {{}};
for (let r = 1; r <= TOTAL_ROUNDS; r++) {{
  const playing = new Set();
  MATCHES.filter(m => m[0] === r).forEach(([,,, home, away]) => {{
    playing.add(home); playing.add(away);
  }});
  const byeTeams = teamNames.filter(t => !playing.has(t));
  if (byeTeams.length) byes[r] = byeTeams;
}}

// Standings
const hasScores = MATCHES.some(m => m[5] !== null);
const stats = {{}};
teamNames.forEach(t => stats[t] = {{ p: 0, w: 0, l: 0, d: 0, pf: 0, pa: 0, byes: 0 }});
for (const [r, bt] of Object.entries(byes)) bt.forEach(t => stats[t].byes++);

if (hasScores) {{
  MATCHES.forEach(([,,,home, away, hScore, aScore]) => {{
    if (hScore === null) return;
    stats[home].p++; stats[away].p++;
    stats[home].pf += hScore; stats[home].pa += aScore;
    stats[away].pf += aScore; stats[away].pa += hScore;
    if (hScore > aScore) {{ stats[home].w++; stats[away].l++; }}
    else if (hScore < aScore) {{ stats[away].w++; stats[home].l++; }}
    else {{ stats[home].d++; stats[away].d++; }}
  }});
}}

// Sort: focus team first, then opponents in round order, then rest
let order;
if (FOCUS && teamNames.includes(FOCUS)) {{
  const focusOpps = [];
  for (let r = 1; r <= TOTAL_ROUNDS; r++) {{
    const m = MATCHES.find(([rd,,, tA, tB]) => rd === r && (tA === FOCUS || tB === FOCUS));
    if (m) {{
      const opp = m[3] === FOCUS ? m[4] : m[3];
      if (!focusOpps.includes(opp)) focusOpps.push(opp);
    }} else {{
      // Focus team has a bye this round — insert a BYE marker
      focusOpps.push(null);
    }}
  }}
  // Remove BYE markers, they're just for ordering
  const oppsClean = focusOpps.filter(t => t !== null);
  const rest = teamNames.filter(t => t !== FOCUS && !oppsClean.includes(t));
  if (hasScores) {{
    rest.sort((a, b) => {{
      const sa = stats[a], sb = stats[b];
      if (sb.w !== sa.w) return sb.w - sa.w;
      return (sb.pf - sb.pa) - (sa.pf - sa.pa);
    }});
  }} else {{
    rest.sort((a, b) => a.localeCompare(b));
  }}
  order = [FOCUS, ...oppsClean, ...rest];
}} else {{
  order = [...teamNames].sort((a, b) => {{
    if (!hasScores) return a.localeCompare(b);
    const sa = stats[a], sb = stats[b];
    if (sb.w !== sa.w) return sb.w - sa.w;
    return (sb.pf - sb.pa) - (sa.pf - sa.pa);
  }});
}}

// Build focus team's round-by-round schedule (for bye display)
const focusSchedule = [];
if (FOCUS && teamNames.includes(FOCUS)) {{
  for (let r = 1; r <= TOTAL_ROUNDS; r++) {{
    const m = MATCHES.find(([rd,,, tA, tB]) => rd === r && (tA === FOCUS || tB === FOCUS));
    if (m) {{
      const opp = m[3] === FOCUS ? m[4] : m[3];
      focusSchedule.push({{ round: r, opponent: opp, time: m[1], court: m[2] }});
    }} else {{
      focusSchedule.push({{ round: r, opponent: null, time: '', court: '' }});
    }}
  }}
}}

// Legend
const legendEl = document.getElementById('legend');
order.forEach(t => {{
  const item = document.createElement('span');
  item.className = 'legend-item';
  item.innerHTML = `<span class="legend-swatch" style="border-color:${{TEAMS[t].color}}"></span><span style="color:${{TEAMS[t].color}}; font-weight:600">${{t}}</span>`;
  legendEl.appendChild(item);
}});

// Matrix
const table = document.getElementById('matrix');
let html = '<thead><tr><th class="sticky-col"></th>';
order.forEach(t => {{
  const fc = (FOCUS && t === FOCUS) ? ' highlight-col' : '';
  html += `<th class="${{fc}}" style="color:${{TEAMS[t].color}}">${{t}}</th>`;
}});
html += '</tr></thead><tbody>';

order.forEach(rowT => {{
  const isHL = FOCUS && rowT === FOCUS;
  html += `<tr class="${{isHL ? 'highlight-row' : ''}}">`;
  html += `<th class="sticky-col" style="color:${{TEAMS[rowT].color}}">${{rowT}}</th>`;
  order.forEach(colT => {{
    const fc = (FOCUS && colT === FOCUS) ? ' highlight-col' : '';
    if (rowT === colT) {{ html += `<td class="diagonal${{fc}}"></td>`; return; }}
    const m = matchMap[rowT]?.[colT];
    if (!m) {{ html += `<td class="empty-cell${{fc}}"></td>`; return; }}

    let scoreHtml = '';
    if (m.myScore !== null) {{
      let sc = 'draw';
      if (m.myScore > m.theirScore) sc = 'win';
      else if (m.myScore < m.theirScore) sc = 'loss';
      scoreHtml = `<div class="cell-score"><span class="${{sc}}">${{m.myScore}} - ${{m.theirScore}}</span></div>`;
    }} else {{
      scoreHtml = '<div class="cell-score"><span class="upcoming">v</span></div>';
    }}

    html += `<td class="${{fc}}">
      <div class="cell-round">R${{m.round}}</div>
      ${{scoreHtml}}
      <div class="cell-time">${{m.time}}</div>
      <div class="cell-court">${{m.court}}</div>
    </td>`;
  }});
  html += '</tr>';
}});
html += '</tbody>';
table.innerHTML = html;

// Note
const noteEl = document.getElementById('note');
if (hasScores) {{
  noteEl.innerHTML = 'Each cell shows the round, score (from row team perspective), time &amp; court.<br><span style="color:#2e7d32;font-weight:700">Green</span> = win, <span style="color:#c62828;font-weight:700">Red</span> = loss';
}} else {{
  noteEl.innerHTML = 'Each cell shows the round, time &amp; court. Scores will appear after each game.';
}}
if (Object.keys(byes).length) {{
  noteEl.innerHTML += '<br>Odd number of teams — one team has a bye each round.';
}}

// Byes — show in focus team's perspective order
const byesEl = document.getElementById('byes');
if (Object.keys(byes).length) {{
  let bh = '<strong>Byes:</strong> ';
  // If focus team exists, show their bye prominently first, then others in round order
  if (FOCUS && teamNames.includes(FOCUS)) {{
    for (let r = 1; r <= TOTAL_ROUNDS; r++) {{
      if (byes[r] && byes[r].includes(FOCUS)) {{
        bh += `R${{r}} <span style="color:${{TEAMS[FOCUS].color}}; font-weight:700">${{FOCUS}} (bye)</span> `;
      }}
    }}
    for (let r = 1; r <= TOTAL_ROUNDS; r++) {{
      if (byes[r]) byes[r].filter(t => t !== FOCUS).forEach(t => {{
        bh += `R${{r}} <span style="color:${{TEAMS[t].color}}">${{t}}</span> `;
      }});
    }}
  }} else {{
    for (let r = 1; r <= TOTAL_ROUNDS; r++) {{
      if (byes[r]) byes[r].forEach(t => {{
        bh += `R${{r}} <span style="color:${{TEAMS[t].color}}">${{t}}</span> `;
      }});
    }}
  }}
  byesEl.innerHTML = bh;
}}

// Standings table
if (hasScores) {{
  const stEl = document.getElementById('standings');
  let sh = '<thead><tr><th>#</th><th>Team</th><th>P</th><th>W</th><th>L</th><th>D</th><th>PF</th><th>PA</th><th>+/-</th></tr></thead><tbody>';
  order.forEach((t, i) => {{
    const s = stats[t];
    const diff = s.pf - s.pa;
    const ds = diff > 0 ? `+${{diff}}` : `${{diff}}`;
    sh += `<tr><td>${{i+1}}</td><td style="color:${{TEAMS[t].color}}">${{t}}</td><td>${{s.p}}</td><td>${{s.w}}</td><td>${{s.l}}</td><td>${{s.d}}</td><td>${{s.pf}}</td><td>${{s.pa}}</td><td style="color:${{diff>=0?'#2e7d32':'#c62828'}}">${{ds}}</td></tr>`;
  }});
  sh += '</tbody>';
  stEl.innerHTML = sh;
  document.getElementById('standings-section').style.display = '';
}}

document.getElementById('updated').textContent = 'Last updated: {updated_time}';
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Generate fixture matrix HTML")
    parser.add_argument("--data", default="data.json", help="Scraped data JSON file")
    parser.add_argument("--config", default="config.json", help="Config JSON file")
    parser.add_argument("--output", "-o", default="index.html", help="Output HTML file")
    args = parser.parse_args()

    with open(args.data) as f:
        data = json.load(f)
    with open(args.config) as f:
        config = json.load(f)

    html = generate_html(data, config)

    with open(args.output, "w") as f:
        f.write(html)
    print(f"Generated {args.output}")


if __name__ == "__main__":
    main()

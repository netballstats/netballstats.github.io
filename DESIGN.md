# PlayHQ Tracker -- Design

## Use Cases

### 1. Carnival Tracking
One-day tournament. Cherry-pick specific grades of interest.
Fetch fixtures before the day, refresh scores during/after.

### 2. Weekly Competition Tracking
Full season (e.g. Winter 2025). All grades, every round.
Refresh weekly to pull latest results.

---

## Data Model

### Entity Relationships

```
Organisation (e.g. Penrith District Netball Association)
  |
  +-- Competition (e.g. "Juniors Winter U9's to U17's")
        |
        +-- Season (e.g. "Winter 2025")  <-- tracked flag lives here
              |
              +-- Grade (e.g. "11A", "U15 Girls Dev")  <-- tracked flag here too
                    |
                    +-- Round (e.g. "Round 1", "Semi Final")
                          |
                          +-- Game (home vs away, score, venue, court)
                          +-- Bye (team with a bye this round)
```

### Tracking Granularity

Two levels of tracking, supporting both use cases:

- **Track a season** --> refreshes ALL grades in that season (weekly comp)
- **Track a grade** --> refreshes just that grade (carnival cherry-pick)

A grade is refreshed if:
- Its own `tracked` flag is set, OR
- Its parent season's `tracked` flag is set

### API Call Flow

```
                          PlayHQ GraphQL API
                          ==================

  [org_id]                [season_id]              [grade_id]
     |                        |                        |
     v                        v                        v
discoverCompetitions  gradeListDiscoverSeason    gradeAllRounds
     |                        |                        |
     v                        v                        v
  Competitions            Grades list            Rounds + Games
  + Seasons               (id, name,             (fixtures, scores,
  (id, name, dates)        gender, age)           teams, venues)
```

### Refresh Flow

```
  playhq_api.py refresh
         |
         v
  Query DB: all seasons/grades where tracked=1
         |
         +---> For each tracked season:
         |       fetch_grades(season_id)
         |       for each grade: fetch_grade_fixtures(grade_id)
         |       upsert rounds + games into DB
         |
         +---> For each individually tracked grade (season not tracked):
                 fetch_grade_fixtures(grade_id)
                 upsert rounds + games into DB
```

---

## Database Schema (SQLite)

```sql
-- Organisations we've seen
CREATE TABLE organisations (
    id          TEXT PRIMARY KEY,   -- PlayHQ hex ID e.g. "414b289d"
    name        TEXT NOT NULL,
    tenant      TEXT NOT NULL DEFAULT 'netball-australia'
);

-- A competition belongs to an org (e.g. "Juniors Winter")
CREATE TABLE competitions (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES organisations(id),
    name        TEXT NOT NULL
);

-- A season belongs to a competition (e.g. "Winter 2025")
CREATE TABLE seasons (
    id          TEXT PRIMARY KEY,
    comp_id     TEXT NOT NULL REFERENCES competitions(id),
    name        TEXT NOT NULL,
    start_date  TEXT,               -- ISO date
    end_date    TEXT,
    status      TEXT,               -- ACTIVE, COMPLETED, etc.
    tracked     INTEGER NOT NULL DEFAULT 0,
    last_refreshed TEXT             -- ISO datetime of last refresh
);

-- A grade belongs to a season (e.g. "11A")
CREATE TABLE grades (
    id          TEXT PRIMARY KEY,
    season_id   TEXT NOT NULL REFERENCES seasons(id),
    name        TEXT NOT NULL,
    gender      TEXT,               -- Girls, Boys, Mixed, etc.
    age_group   TEXT,               -- U12, U15, Open, etc.
    tracked     INTEGER NOT NULL DEFAULT 0,
    last_refreshed TEXT
);

-- A round belongs to a grade (e.g. "Round 1")
CREATE TABLE rounds (
    id          TEXT PRIMARY KEY,
    grade_id    TEXT NOT NULL REFERENCES grades(id),
    name        TEXT NOT NULL,
    is_finals   INTEGER NOT NULL DEFAULT 0
);

-- A game belongs to a round
CREATE TABLE games (
    id          TEXT PRIMARY KEY,
    round_id    TEXT NOT NULL REFERENCES rounds(id),
    date        TEXT,               -- ISO date
    time        TEXT,               -- HH:MM:SS
    venue       TEXT,
    court       TEXT,
    home_team   TEXT,
    home_team_id TEXT,
    away_team   TEXT,
    away_team_id TEXT,
    home_score  INTEGER,
    away_score  INTEGER,
    status      TEXT,               -- scheduled, completed, cancelled, etc.
    outcome     TEXT                -- DRAW_BY_SCORE, HOME_WIN, AWAY_WIN, etc.
);

-- Byes belong to a round
CREATE TABLE byes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id    TEXT NOT NULL REFERENCES rounds(id),
    team_name   TEXT NOT NULL,
    team_id     TEXT
);
```

---

## CLI Interface

```
# ---- Discovery (read-only, prints to stdout) ----

playhq_api.py org <org_id>                  # list competitions + seasons
playhq_api.py season <season_id>            # list grades
playhq_api.py grade <grade_id>              # show fixtures + scores

# ---- Tracking (writes to DB) ----

playhq_api.py track <season_id>             # track season (all grades)
playhq_api.py track <grade_id>              # track single grade
playhq_api.py untrack <season_id|grade_id>  # stop tracking
playhq_api.py tracked                       # list everything being tracked

# ---- Refresh (fetches API, updates DB) ----

playhq_api.py refresh                       # refresh all tracked items
playhq_api.py refresh <season_id|grade_id>  # refresh one item only

# ---- Reporting (reads from DB) ----

playhq_api.py results <grade_id>            # print fixture results from DB
playhq_api.py results <season_id>           # all grades in season from DB
```

### Example Workflows

**Carnival day:**
```bash
# Find the carnival
playhq_api.py org 414b289d
playhq_api.py season deaa06cd

# Track grades I care about
playhq_api.py track 15299ca4    # U15 Girls Dev
playhq_api.py track 2562b253    # U15 State BLACK

# Refresh during the day
playhq_api.py refresh

# Check scores
playhq_api.py results 15299ca4
```

**Weekly comp:**
```bash
# Find the season
playhq_api.py org 414b289d

# Track the whole season
playhq_api.py track 5f52fed2    # Winter 2025

# Refresh each week
playhq_api.py refresh

# Check results
playhq_api.py results 5f52fed2
```

---

## Design Decisions

1. **IDs as primary keys** -- PlayHQ hex IDs are stable and unique; no need for auto-increment surrogates.

2. **Upsert on refresh** -- Games are inserted or updated (by ID) so re-running refresh is safe and idempotent.

3. **Track at two levels** -- Season-level tracking for weekly comps (don't want to track 20+ grades individually). Grade-level for carnivals (only care about 2-3 grades).

4. **Tenant stored on org** -- Supports future expansion beyond netball-australia if needed.

5. **No teams table** -- Team names are denormalized into games. Teams in PlayHQ are season-specific anyway (a club re-registers each season), so normalizing them adds complexity with little benefit.

6. **Byes are separate** -- They don't have scores, opponents, or venues. Keeping them in the games table would mean lots of NULLs.

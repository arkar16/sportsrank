# Option A Plan: Dynamic SSR site backed by SQLite

Draft companion plan to [proposed ADR-0033](../adr/0033-serve-dynamic-html-from-sqlite.md).
This is a design and migration blueprint; nothing is activated or deployed until
owner approval.

## 1. Target architecture

```text
Offline generator (MacBook / Desktop 5070 / Raspberry Pi)
  ├─ Metered source fetch (CFBD / CBB / NFL / NBA / Soccer) -> raw private cache
  ├─ Ranking & forecast engines (Python: cfb/, cbb/, ...)
  └─ Release builder -> immutable, read-only sportsrank.sqlite3 + static assets (~650 files)
                              │
                              ▼
             Stateless SSR Web Tier + Edge CDN Cache
  ├─ Clean canonical routes (/cfb/2026, /cfb/teams/alabama/2026, /cbb/...)
  ├─ Legacy .html compatibility routes / 301 redirects (zero broken links)
  ├─ Shared Jinja2 / HTML templates + record-book.css (zero required JS)
  └─ Cache-Control + ETag headers (historical pages cached long-term at edge)
```

### Key architectural invariants

- **Zero live API calls in the web tier.** The server opens `sportsrank.sqlite3`
  in read-only mode (`?mode=ro&immutable=1` when baked into an image) and holds
  no `CFBD_API` or `CFBD_PRED_API` secrets.
- **Atomic releases.** Every weekly/daily update produces a validated database
  snapshot (and release ID). Publishing swaps the active DB/container atomically;
  rollback means pointing back to the previous release artifact.
- **Pure HTML/CSS output.** Keeps [ADR-0007](../adr/0007-use-a-statistical-reference-presentation.md)
  intact—same newspaper typography, tables, and cached school marks, now driven
  by one shared base layout template (one masthead for all sports).

---

## 2. Clean canonical URLs & legacy `.html` compatibility

All internal links use clean, extensionless URLs. Every previously published
`.html` URL from the current 34,923-file archive is handled explicitly.

### Canonical routes

| View | Canonical URL |
| --- | --- |
| Home | `/` |
| Sport home (latest rankings + archive) | `/{sport}` (e.g. `/cfb`, `/cbb`, `/nfl`) |
| Season standings (latest or FINAL) | `/{sport}/{season}` (e.g. `/cfb/2026`, `/cbb/2026-27`) |
| Season checkpoint | `/{sport}/{season}/{checkpoint}` (e.g. `/cfb/2026/w11`, `/cfb/2026/preseason`, `/cfb/2026/final`) |
| Weekly scores / schedule / spreads | `/{sport}/{season}/w{wk}/results`, `.../schedule`, `.../spreads` |
| Programs index | `/{sport}/teams` |
| Program history | `/{sport}/teams/{slug}` |
| Program season log | `/{sport}/teams/{slug}/{season}` |
| Model performance index & season | `/{sport}/performance`, `/{sport}/performance/{season}` |
| Honors (champions / last place) | `/{sport}/history/champions`, `/{sport}/history/last-place` |
| Dated pregame edition | `/{sport}/editions/{timestamp}` |
| Machine-readable exports | `/{sport}/performance/{season}.json`, `.../{season}.csv` |
| Static assets (CSS, fonts, marks) | `/assets/...` (with `/cfb/assets/...` alias preserved) |

### Legacy `.html` compatibility matrix

| Legacy path pattern | Count today | Handling |
| --- | ---: | --- |
| `/cfb/cfb.html` | 1 | `301` → `/cfb` |
| `/cfb/teams/index.html` | 1 | `301` → `/cfb/teams` |
| `/cfb/teams/{slug}/index.html` | 302 | `301` → `/cfb/teams/{slug}` |
| `/cfb/teams/{slug}/{year}.html` | 14,174 | `301` → `/cfb/teams/{slug}/{year}` |
| `/cfb/years/{year}/{year}_CFB.html` | 130 | `301` → `/cfb/{year}` |
| `/cfb/years/{year}/checkpoints/{cp}.html` | 2,539 | `301` → `/cfb/{year}/{cp_lower}` |
| `/cfb/years/{year}/rankings/{year}_{cp}_FBS_cors.html` | 2,654 | `301` → `/cfb/{year}/{cp_lower}` (or serve raw table if `?raw=1`) |
| `/cfb/years/{year}/data/results/weekly_results/{year}_W{wk}_FBS_results.html` | ~2,400 | `301` → `/cfb/{year}/w{wk}/results` |
| `/cfb/years/{year}/data/slate/weekly_slate/{year}_W{wk}_FBS_slate.html` | ~2,400 | `301` → `/cfb/{year}/w{wk}/schedule` |
| `/cfb/years/{year}/spread/{year}_W{wk}_FBS_spread.html` | ~1,700 | `301` → `/cfb/{year}/w{wk}/spreads` |
| `/cfb/years/{year}/spread/{year}_W{wk}_FBS_spread_results.html` | ~1,700 | `301` → `/cfb/{year}/w{wk}/spreads#results` (or dedicated graded route) |
| `/cfb/years/{year}/data/records/{year}_W{wk}_FBS_records.html` | 2,407 | Serve dynamically or `301` → `/cfb/{year}/w{wk}` *(open decision)* |
| `/cfb/years/{year}/reconstructed/{year}_W{wk}_FBS_spread.html` | 2,338 | Serve dynamically or `301` → `/cfb/{year}/w{wk}/spreads` *(open decision)* |
| `/cfb/performance/index.html` & `/cfb/performance/{year}/index.html` | 131 | `301` → `/cfb/performance` & `/cfb/performance/{year}` |
| `/cfb/history/nc_FBS_CFB_output.html` & `/cfb/years/history/nc_FBS_CFB_output.html` | 2 | `301` → `/cfb/history/champions` |
| `/cfb/history/wt_FBS_CFB_output.html` & `/cfb/years/history/wt_FBS_CFB_output.html` | 2 | `301` → `/cfb/history/last-place` |
| `/cbb/index.html` & `/cbb/editions/{ts}.html` | 2 | `301` → `/cbb` & `/cbb/editions/{ts}` |

---

## 3. SQLite read-model schema (multi-sport)

One SQLite file (`sportsrank.sqlite3`, ~40–100 MB across CFB + CBB + future
sports) replaces ~820 MB of baked HTML. Core tables:

- `releases (release_id, built_at_utc, git_sha, schema_version, notes)`
- `sports (sport_id, slug, name, default_classification, current_season)`
- `programs (program_id, sport_id, slug, canonical_name, mark_asset_path)`
- `program_aliases (alias_id, program_id, alias_name, first_season, last_season)`
- `seasons (season_id, sport_id, classification, label, year, is_final, updated_at_utc)`
- `checkpoints (checkpoint_id, season_id, code, sort_order, cutoff_utc, label)`
- `rankings (checkpoint_id, program_id, rank, rating, wins, losses, ties, record_text, conference, mov, sos, wins_vs_expected)`
- `games (game_id, season_id, week, phase, start_utc, neutral_site, home_program_id, away_program_id, home_score, away_score, status)`
- `forecasts (forecast_id, game_id, checkpoint_id, issued_at_utc, is_reconstructed, predicted_home_margin, market_home_spread, market_provider)`
- `editions (edition_id, sport_id, season_id, timestamp_slug, cutoff_utc, prepared_at_utc, payload_json)`

All hot queries (`rankings` by `(checkpoint_id, rank)` and `(program_id, checkpoint_id)`;
`games` by `(season_id, week)` and `(home_program_id)` / `(away_program_id)`)
are covered by composite indexes for <2 ms local reads.

---

## 4. Hosting options & recommendation

Because the database is read-only per release and pages are aggressively
cacheable at the CDN edge, compute usage is tiny.

| Option | Monthly cost | Python-native? | Cold starts / sleep | Ops / risk notes |
| --- | --- | --- | --- | --- |
| **GCP Cloud Run + existing Firebase Hosting** | **$0** within Always Free (2M req, 180k vCPU-s, 360k GiB-s, 10 GB egress) | **Yes** (FastAPI/Starlette + Jinja2 + SQLite inside image) | ~0.5–1.5s when scaled to zero (masked by Firebase CDN `s-maxage`) | Keeps `sportsrank-837af` & `sportsrank.top`. Requires Blaze card + `max-instances` cap + budget alerts. |
| **Cloudflare Workers + D1 + Static Assets** | **$0** (100k req/day, 5M D1 reads/day, 500 MB/DB, 20k static assets) | **No** (thin TS/Hono renderer; Python keeps all modeling/DB builds) | ~0 ms (edge isolates) | Hard free-tier cutoffs = zero surprise bills. Initial D1 bulk load needs chunking around 100k writes/day free cap. |
| **Raspberry Pi + Cloudflare Tunnel** | **$0** (hardware/power already owned) | **Yes** | None (always on) | Outbound-only tunnel (no open router ports) + Cloudflare CDN caching. Tied to home internet/power uptime. |
| **Desktop 5070 + Cloudflare Tunnel** | $0 (higher electricity) | **Yes** | None while awake | **Best for local dev/staging only**—desktop sleep/reboots/updates make it a poor public origin. |
| **Railway** | $1/mo free credit (sleeps after 5–10m; pauses when credit spent) or **$5/mo Hobby** | Yes | Sleeps on free; none on $5 Hobby | Cleanest paid PaaS if willing to spend $5/mo; free plan isn't reliable for a public site. |
| **Render** | $0 Hobby (sleeps after 15m) or **$7/mo Starter** | Yes | **~50–60s cold start** on free | Free sleep penalty is too slow for clicking team/season links. |
| **Fly.io** | No permanent free tier (~**$2–3/mo** min) | Yes | Optional auto-stop | Good low-cost container host if paying a few dollars/month. |
| **AWS Lambda + CloudFront** | **$0** Always Free (1M req/mo + 1 TB/mo CloudFront) | Yes | ~0.5–1s | More IAM/infra boilerplate than Cloud Run for no real benefit here. |

### Recommended path

1. **Right now (build & validate):** Develop and test locally on the **Desktop 5070** (and optionally stage on the **Raspberry Pi** over a private/staging Cloudflare Tunnel).
2. **Production cutover (pick one based on billing preference):**
   - **Pick GCP Cloud Run + existing Firebase Hosting** if you want **100% Python**, zero rewrite of rendering helpers, atomic SQLite-in-container deploys, and staying on your current Firebase/GCP project (with `max-instances=2` + $5 budget alert so Blaze stays at $0).
   - **Pick Cloudflare Workers + D1** if you want **strict $0 with hard cutoffs and zero credit-card risk**, and don't mind a thin ~400-line TypeScript SSR layer while Python stays in charge of all CORS math and DB generation.
   - **Pick Raspberry Pi + Cloudflare Tunnel** only if you want zero cloud accounts/billing and accept home-internet availability for the public site.

---

## 5. Phased migration plan

### Phase 1 — Build the SQLite exporter & verify historical parity (offline)
- Create a read-model builder (`sportsrank/export_db.py`) that populates
  `sportsrank.sqlite3` from existing CFB archived inputs/snapshots and CBB
  `.sportsrank/cbb/history.sqlite3` without live provider calls.
- Verify every CFB season (1897–2026), all 303 program histories, and current
  CBB rankings reconcile numerically against the committed `website/` archive.

### Phase 2 — Build the thin SSR web app + legacy redirect layer (local)
- Build a small SSR app (`web/` — FastAPI/Starlette + Jinja2 for Python, or
  Hono for Workers) sharing one base masthead template across CFB, CBB, and
  future sports.
- Reuse `website/cfb/assets/record-book.css`, font, and cached school marks
  (optimize large PNGs in a separate pass if desired).
- Add automated route tests verifying:
  - Every canonical page returns valid, complete HTML with no JS dependency.
  - Every single `.html` path in the current `website/` inventory (all 33,413
    paths) returns either `200` or `301` to a live `200` destination—zero `404`s.

### Phase 3 — Staging deployment & live parity check
- Deploy to a staging URL (Cloud Run revision / preview channel, Workers dev
  domain, or Pi Cloudflare Tunnel).
- Spot-check rendered HTML, phone layout, school marks, and legacy bookmarks
  against live `sportsrank.top`.

### Phase 4 — Production cutover & repository cleanup
- Switch `sportsrank.top` traffic to the SSR origin behind CDN caching.
- Verify post-cutover checks across current CFB, CBB, program pages, and sampled
  legacy `.html` URLs.
- Only after cutover is verified and a final static archive tag/backup is saved,
  remove generated HTML from `website/` in Git (keeping CSS/font/school marks or
  moving them to `assets/`).

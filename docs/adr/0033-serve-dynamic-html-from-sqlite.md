---
status: accepted
---

# Serve dynamic HTML from a versioned SQLite release database

SportsRank will replace its pre-generated `.html` file tree with server-rendered
HTML served directly from a versioned, read-only SQLite database, using clean
canonical URLs and permanent compatibility redirects for every previously
published `.html` URL.

## Context

Serving pre-baked `.html` files from Git through Firebase Hosting worked for a
single sport with a small archive, but has reached its scaling limit as of
November 2026:

- `website/` on `main` has grown to **87,737 tracked files** (**84,342 `.html`
  files**, ~2.3 GB uncompressed) across college football, 79 seasons of men's
  college basketball, conferences, OOC conference matchups, excitement
  leaderboards, March Madness tournaments, and shareable pick cards.
- Small sitewide changes (link updates, copy edits, masthead navigation) rewrite
  tens of thousands of files per commit and require a reversible regex chrome
  layer (`<!--sr-ui-->`) because legacy code still parses published HTML tables.
- Planned expansion into NFL, NBA, and soccer (alongside interactive features
  like matchup calculators and filtered tables) will multiply static file counts
  and deploy fragility if pre-baking continues.

## Decision

1. **Read-only SQLite as the serving source of truth.** Offline Ranking Runs
   build and validate a self-contained SQLite release database (`sportsrank.sqlite3`)
   containing normalized CFB/CBB relational data alongside compressed clean-URL
   content for specialized views (conferences, excitement, picks, tournaments)
   during incremental migration. Published HTML is served dynamically at request
   time and is no longer committed to Git once cutover completes.
2. **Server-rendered semantic HTML/CSS, not an SPA.** Every public page returns
   complete, inspectable HTML and CSS on the initial HTTP response with no
   client-side JavaScript required for core content, preserving ADR-0002 and
   ADR-0007 (retaining existing progressive enhancements such as table sorting
   and pick-card sharing).
3. **Clean canonical URLs with full legacy `.html` preservation.** All internal
   links move to extensionless canonical routes (for example `/cfb/2026`,
   `/cfb/teams/alabama/2026`, `/cfb/conferences/sec/2026`, `/cbb/seasons/2026`,
   `/excitement/grades`). Every previously published `.html` URL under ADR-0006
   remains publicly addressable via permanent `301` redirect to its live
   canonical destination.
4. **Aggressive HTTP caching at the CDN edge.** Historical seasons, completed
   conferences, and dated editions are immutable per release and served with
   long-lived `Cache-Control` headers and `ETag`s so public traffic hits edge
   cache rather than origin compute.
5. **Runtime hosting remains decoupled from source acquisition.** CFBD/CBBD
   fetches stay strictly offline behind ADR-0009's Request Meter. The public web
   tier holds no provider credentials and performs zero upstream API calls.

## Relationship to earlier ADRs

- **Supersedes (when accepted):**
  - ADR-0002's requirement that every page be pre-generated as a static file on
    disk (retaining its core rule: complete HTML/CSS without client-side JS
    rendering).
  - ADR-0008's restriction of SQLite to generation-time-only state behind static
    file uploads.
  - ADR-0026's direct deployment of a committed `website/` HTML tree.
- **Modifies (when accepted):**
  - ADR-0003's public hosting origin (adding Cloud Run behind Firebase Hosting
    CDN rewrites in `sportsrank-837af`).
- **Preserves:**
  - ADR-0006 (every previously published artifact URL remains addressable).
  - ADR-0007 (barebones newspaper statistical-reference presentation).
  - ADR-0009 (metered, offline source boundary; zero live provider calls in
    serving or tests).
  - ADR-0019, ADR-0020, ADR-0021, ADR-0027, ADR-0028, and ADRs 0029–0032
    (forecast provenance, excitement scoring, program histories, and multi-sport
    model decisions).

## Rollout & cutover gates

1. **Phase 1–2 (Offline DB + WSGI app):** Export `sportsrank.sqlite3` from
   `main`'s full archive, verify zero redirect or destination failures across
   all 84,341 legacy `.html` paths, and pass offline unit tests.
2. **Phase 3 (Cloud Run staging):** Deploy `sportsrank-web` (`us-central1`,
   `--min-instances=0`, `--max-instances=2`) via manual GitHub Actions
   dispatch without touching live `sportsrank.top`.
3. **Phase 4 (Production cutover, separate owner approval):** Route
   `sportsrank.top` to `sportsrank-web` via `firebase.json` rewrites, verify
   live traffic and legacy bookmarks, and only then retire generated HTML from
   Git.

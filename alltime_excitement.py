"""All-time record-book pages built from the frozen scores in the season guides."""
from dataclasses import asdict, dataclass
from hashlib import sha256
from html import escape
import json
from pathlib import Path

from excitement_grades import LETTERS, MINIMUMS, SCALE_LINK, letter_grade
from site_navigation import masthead


SPORTS = {"cfb": "College football", "cbb": "Men’s basketball"}
PROVIDERS = {"cfb": "CollegeFootballData", "cbb": "CollegeBasketballData"}
METRICS = ("AEV", "BEV")
SUMMARY = "leaders-v1.json"


def football_anchor(key):
    # Some old games have no retained provider ID. The archive matchup also
    # distinguishes same-week rematches and survives upgrades in score evidence.
    identity = json.dumps(key, ensure_ascii=False, separators=(",", ":")).encode()
    return "fixture-" + sha256(identity).hexdigest()[:20]


@dataclass(frozen=True)
class Game:
    sport: str
    year: int
    when: str
    away: str
    home: str
    anchor: str
    bev: str
    aev: str | None
    final: str
    bev_basis: str
    aev_basis: str

    @classmethod
    def from_score(cls, sport, year, when, away, home, anchor, score):
        final = "" if score.final is None else f"{score.final[1]}–{score.final[0]}"
        aev = f"{score.aev:.1f}" if score.aev is not None and score.final is not None else None
        return cls(sport, year, when, away, home, anchor, score.bev, aev, final,
                   score.bev_basis or "Pregame CORS", score.evidence_label if aev is not None else "")

    @property
    def href(self):
        folder = "years" if self.sport == "cfb" else "seasons"
        return f"/{self.sport}/{folder}/{self.year}/excitement/index.html#{self.anchor}"


def _top(games, metric):
    """Keep the tenth game and everyone tied with it at the published precision."""
    field = metric.lower()
    ranked = sorted(games, key=lambda game: (-float(getattr(game, field)), game.year,
                                             game.when, game.away, game.home, game.sport, game.anchor))
    if len(ranked) <= 10:
        return ranked
    cutoff = getattr(ranked[9], field)
    return ranked[:10] + [game for game in ranked[10:] if getattr(game, field) == cutoff]


class Leaders:
    def __init__(self):
        self.counts = dict.fromkeys(METRICS, 0)
        self.games = {metric: [] for metric in METRICS}

    def add_season(self, games):
        games = list(games)
        for metric in METRICS:
            eligible = games if metric == "BEV" else [game for game in games if game.aev is not None]
            self.counts[metric] += len(eligible)
            self.games[metric] = _top(self.games[metric] + eligible, metric)

    def save(self, root, sport):
        # The other sport can refresh the combined pages from this small
        # summary without loading a provider database or replaying this sport.
        data = {"provider": PROVIDERS[sport], "counts": self.counts,
                "games": {key: [asdict(game) for game in rows] for key, rows in self.games.items()}}
        return _write(root / "excitement" / SUMMARY, json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    @classmethod
    def read(cls, raw, sport):
        data = json.loads(raw)
        if data["provider"] != PROVIDERS[sport]:
            raise ValueError(f"Unexpected {sport} game excitement source")
        leaders = cls()
        leaders.counts = data["counts"]
        leaders.games = {metric: [Game(**g) for g in data["games"][metric]] for metric in METRICS}
        return leaders


def _write(path, text):
    from cfb.program_marks import _atomic_write

    data = text.encode("utf-8")
    if path.is_file() and path.read_bytes() == data:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, data)
    return 1


def _url(scope, metric):
    prefix = f"/{scope}" if scope else ""
    return f"{prefix}/excitement/{metric.lower()}.html"


def _links(options, active):
    return " · ".join(f'<strong aria-current="page">{label}</strong>' if key == active
                      else f'<a href="{url}">{label}</a>' for key, label, url in options)


def _page(scope, metric, games, count, scopes):
    field = metric.lower()
    sport_name = SPORTS.get(scope, "All sports")
    name = "After Excitement Value" if metric == "AEV" else "Before Excitement Value"
    intro = (f"Game drama across {count:,} verified finals." if metric == "AEV" else
             f"Pregame appeal across {count:,} matchups, including scheduled games.")
    scope_links = _links([(key, SPORTS.get(key, "All sports"), _url(key, metric)) for key in scopes], scope)
    metric_links = _links([(m, m, _url(scope, m)) for m in METRICS], metric)
    rows = []
    previous = None
    for position, game in enumerate(games, 1):
        value = getattr(game, field)
        if value != previous:
            rank = position
            previous = value
        evidence = game.aev_basis if metric == "AEV" else game.bev_basis
        star = "*" if metric == "AEV" and evidence != "Play-by-play" else ""
        season = str(game.year) if game.sport == "cfb" else f"{game.year-1}–{str(game.year)[-2:]}"
        sport_cell = "" if scope else f"<td>{'CFB' if game.sport == 'cfb' else 'MBB'}</td>"
        grade = letter_grade(game.sport, metric, value)
        rows.append(f'<tr><td class="num">{rank}</td><td class="num"><strong>{value}{star}</strong> ({grade})</td>'
                    f'<th scope="row"><a href="{escape(game.href)}">'
                    f'{escape(game.away)} vs. {escape(game.home)}</a></th>{sport_cell}'
                    f'<td>{season}</td><td>{escape(game.when)}</td><td class="num">{escape(game.final) or "Pending"}</td>'
                    f'<td>{escape(evidence)}</td></tr>')
    headers = ["Rank", metric, "Matchup"] + ([] if scope else ["Sport"]) + ["Season", "Date / week", "Final", "Evidence"]
    table = ('<div class="table-scroll" tabindex="0"><table><thead><tr>'
             + "".join(f'<th scope="col">{h}</th>' for h in headers)
             + '</tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>')
    note = " A star marks an AEV estimate." if metric == "AEV" else ""
    sources = " and ".join(PROVIDERS[s] for s in SPORTS if s in scopes and (not scope or s == scope))
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>All-time {metric} leaders · {sport_name} | SportsRank</title>
<link rel="stylesheet" href="/cfb/assets/record-book.css"></head><body>{masthead()}
<main class="archive-content"><h1>All-time {metric} leaders</h1>
<p class="edition">{sport_name} · {name}</p>
<nav aria-label="Excitement score">{metric_links}</nav><nav aria-label="Sport">{scope_links}</nav>
<p>{intro} Scores run from 0 to 100. Equal scores share a rank, including ties at tenth. {SCALE_LINK}.</p>
{table if rows else "<p>No qualifying games yet.</p>"}
<p>Final scores follow the team order shown.{note} Game data from {sources}.</p>
</main></body></html>
'''


def _grade_scale_page():
    rows = []
    columns = [(sport, metric) for sport in SPORTS for metric in ("BEV", "AEV")]
    for i in reversed(range(len(LETTERS))):
        bounds = [f"{MINIMUMS[key][i - 1]:.1f}" if i else f"Below {MINIMUMS[key][0]:.1f}" for key in columns]
        rows.append(f'<tr><th scope="row">{LETTERS[i]}</th>'
                    + "".join(f'<td class="num">{bound}</td>' for bound in bounds) + "</tr>")
    heads = "".join(f'<th scope="col">{SPORTS[sport]} {metric}</th>' for sport, metric in columns)
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Excitement letter grades | SportsRank</title>
<link rel="stylesheet" href="/cfb/assets/record-book.css"></head><body>{masthead()}
<main class="archive-content"><h1>Excitement letter grades</h1>
<p>A+ marks roughly the top 1% of the reference games for that sport and metric. BEV grades pregame appeal.
AEV grades how the game unfolded. An asterisk marks an estimated AEV and grade.</p>
<div class="table-scroll" tabindex="0"><table><caption>Minimum score for each grade</caption>
<thead><tr><th scope="col">Grade</th>{heads}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>
<p>The cutoffs are fixed. They use ten football seasons (2014–2023) and ten men's basketball seasons
(2014–15 through 2023–24). BEV uses games with pregame CORS ranks and spreads. AEV uses games
with verified play-by-play. A score right on a cutoff earns that grade.</p>
<p><a href="/excitement/bev.html">All-time BEV leaders</a> · <a href="/excitement/aev.html">All-time AEV leaders</a></p>
</main></body></html>
'''


def publish_pages(root: Path, sport: str):
    other = "cfb" if sport == "cbb" else "cbb"

    def snapshot():
        current = {sport: (root / "excitement" / SUMMARY).read_bytes()}
        sibling = root.parent / other / "excitement" / SUMMARY
        if root.name == sport and sibling.is_file():
            current[other] = sibling.read_bytes()
        return current

    changed = 0
    if root.name == sport:
        changed += _write(root.parent / "excitement/grades.html", _grade_scale_page())
    before = snapshot()
    while True:
        changed += _publish(root, sport, before)
        after = snapshot()
        if before == after:
            return changed
        # Two sport builds can finish together. Reconcile against the latest pair.
        before = after


def _publish(root, sport, snapshots):
    boards = {key: Leaders.read(raw, key) for key, raw in snapshots.items()}
    scopes = [s for s in SPORTS if s in boards]
    if root.name == sport:
        scopes.insert(0, "")
    changed = 0
    for scope in scopes:
        target = root if scope == sport else root.parent / scope
        for metric in METRICS:
            active = [boards[scope]] if scope else list(boards.values())
            games = _top([g for board in active for g in board.games[metric]], metric)
            count = sum(board.counts[metric] for board in active)
            changed += _write(target / "excitement" / f"{metric.lower()}.html",
                              _page(scope, metric, games, count, scopes))
    return changed

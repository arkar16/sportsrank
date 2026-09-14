"""Explicit provider-week to SportsRank-week normalization policy.

CFBD's provider week labels are retained as ``provider_week``.  The
SportsRank Week 0 policy below is an application normalization backed by the
season-specific dates cited in the recovery plan; it is not a claim about a
provider-wide conversion rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Final
from zoneinfo import ZoneInfo


EASTERN: Final = ZoneInfo("America/New_York")
CALENDAR_POLICY_ID: Final[str] = "cfb-provider-week-v1"
POSTSEASON_CALENDAR_POLICY_ID: Final[str] = "cfb-postseason-week-lattice-v1"

# Source-backed Week 0/Week 1 evidence supplied for the Gate 1 repair:
# 2024 FSU/Georgia Tech Week 0 schedule (Aug 24, 2024):
# https://seminoles.com/documents/download/2024/8/20/Week_0_vs._Georgia_Tech.pdf
# 2025 Big 12 early-season selections (Aug 23, 2025):
# https://big12sports.com/news/2025/5/29/big-12-announces-early-season-and-special-date-football-tv-selections.aspx
# 2026 NCAA Week 0 recap (Aug 29, 2026):
# https://www.ncaa.com/live-updates/football/fbs/college-football-week-0-recaps-highlights-scores-and-more
# 2026 ACC Week 0 schedule (Aug 29, 2026):
# https://admin.theacc.com/news/2026/8/25/acc-football-kicks-off-with-five-teams-in-action-in-week-0.aspx
WEEK_ZERO_EVIDENCE_URLS: Final[dict[int, tuple[str, ...]]] = {
    2024: (
        "https://seminoles.com/documents/download/2024/8/20/Week_0_vs._Georgia_Tech.pdf",
    ),
    2025: (
        "https://big12sports.com/news/2025/5/29/big-12-announces-early-season-and-special-date-football-tv-selections.aspx",
    ),
    2026: (
        "https://www.ncaa.com/live-updates/football/fbs/college-football-week-0-recaps-highlights-scores-and-more",
        "https://admin.theacc.com/news/2026/8/25/acc-football-kicks-off-with-five-teams-in-action-in-week-0.aspx",
    ),
}

WEEK_ONE_BOUNDARIES: Final[dict[int, datetime]] = {
    2024: datetime(2024, 8, 26, tzinfo=EASTERN),
    2025: datetime(2025, 8, 25, tzinfo=EASTERN),
    2026: datetime(2026, 8, 31, tzinfo=EASTERN),
}


@dataclass(frozen=True)
class PostseasonWindow:
    """Fixed, source-backed local-date bounds for one season's postseason."""

    season: int
    start_date: date
    end_date: date
    source_urls: tuple[str, ...] = ()
    policy_id: str = POSTSEASON_CALENDAR_POLICY_ID


# The bounds are fixed local calendar dates from the verified NCAA postseason
# scoreboards and CFP schedule pages.  They are application policy, not a
# provider conversion rule: provider week/round values never choose these
# dates.  A future season remains fail-closed until an equally explicit window
# and source evidence are added.
POSTSEASON_WINDOWS: Final[dict[int, PostseasonWindow]] = {
    2024: PostseasonWindow(
        season=2024,
        start_date=date(2024, 12, 14),
        end_date=date(2025, 1, 20),
        source_urls=(
            "https://www.ncaa.com/scoreboard/football/fbs/2024/P/all-conf",
            "https://collegefootballplayoff.com/sports/football/schedule/2024-25",
            "https://collegefootballplayoff.com/news/2023/5/2/24-25-dates",
        ),
    ),
    2025: PostseasonWindow(
        season=2025,
        start_date=date(2025, 12, 13),
        end_date=date(2026, 1, 19),
        source_urls=(
            "https://www.ncaa.com/scoreboard/football/fbs/2025/P/all-conf",
            "https://collegefootballplayoff.com/sports/football/schedule/2025-26",
            "https://collegefootballplayoff.com/news/2023/5/2/24-25-dates",
        ),
    ),
    2026: PostseasonWindow(
        season=2026,
        # Bowl Season's first listed postseason date is the Dec 12
        # Celebration Bowl (FCS); the first FBS bowl follows on Dec 15.
        # Keep the source-backed phase window inclusive of that first date.
        start_date=date(2026, 12, 12),
        end_date=date(2027, 1, 25),
        source_urls=(
            "https://bowlseason.com/sports/bowl/schedule/2026-27?grid=true",
            "https://www.salutetoveteransbowl.com/the-game/",
            "https://collegefootballplayoff.com/sports/2018/8/31/cfp-games-schedule",
        ),
    ),
}


def calendar_provenance(season: int) -> dict[str, object]:
    """Return the canonical, source-backed policy identity for one season.

    Releases seal this derived value with each provider-backed snapshot run.
    Callers must derive it from this registry rather than accepting a
    manifest-supplied boundary or evidence URL as authority.
    """

    normalized = require_supported_season(season)
    return {
        "policy_id": CALENDAR_POLICY_ID,
        "season": normalized,
        "boundary": WEEK_ONE_BOUNDARIES[normalized].isoformat(),
        "timezone": str(EASTERN),
        "source_urls": list(WEEK_ZERO_EVIDENCE_URLS[normalized]),
    }


def _postseason_window(season: int) -> PostseasonWindow:
    normalized = require_supported_season(season)
    window = POSTSEASON_WINDOWS.get(normalized)
    if window is None:
        raise ValueError(
            f"No explicit postseason date window configured for season {normalized}"
        )
    if window.start_date > window.end_date:
        raise ValueError("postseason calendar window is inverted")
    if window.start_date < WEEK_ONE_BOUNDARIES[normalized].date():
        raise ValueError("postseason calendar window precedes the Week 1 boundary")
    return window


def postseason_calendar_provenance(
    season: int,
) -> dict[str, object]:
    """Return the independently sealed fixed-window postseason policy."""

    policy = _postseason_window(season)
    return {
        "policy_id": policy.policy_id,
        "season": policy.season,
        "start_date": policy.start_date.isoformat(),
        "end_date": policy.end_date.isoformat(),
        "week_one_boundary": WEEK_ONE_BOUNDARIES[policy.season].isoformat(),
        "timezone": str(EASTERN),
        "source_urls": list(policy.source_urls),
    }


def require_supported_season(season: int) -> int:
    """Return ``season`` only when its explicit calendar policy is known."""

    normalized = int(season)
    if normalized not in WEEK_ONE_BOUNDARIES:
        raise ValueError(
            f"No explicit provider Week 1 boundary configured for season {normalized}"
        )
    return normalized


def _provider_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("provider game date is empty")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("provider game date is invalid") from error
    else:
        raise ValueError("provider game date is invalid")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=EASTERN)
    return parsed.astimezone(EASTERN)


def canonical_week(season: int, provider_week: int, start_date: object) -> int:
    """Map one supported provider Week 1 game to canonical Week 0 when dated.

    Provider weeks other than 1 retain their integer labels.  Supported-season
    Week 1 games require a valid date so missing metadata cannot silently
    produce an empty Week 0 or an invented boundary.
    """

    normalized_season = require_supported_season(season)
    if provider_week != 1:
        return int(provider_week)
    if start_date is None:
        raise ValueError(
            f"provider Week 1 game for season {normalized_season} is missing startDate"
        )
    if _provider_datetime(start_date) < WEEK_ONE_BOUNDARIES[normalized_season]:
        return 0
    return 1


def canonical_postseason_week(
    season: int,
    start_date: object,
) -> int:
    """Map a positively classified postseason game onto the week lattice.

    Postseason does not use provider week/round numbering.  Its canonical
    week is one plus the number of seven-day periods from the existing local
    Week 1 boundary.  The fixed season window is independently required so a
    malformed or shifted date cannot create a plausible regular-week game.
    """

    policy = _postseason_window(season)
    local_day = _provider_datetime(start_date).date()
    if not policy.start_date <= local_day <= policy.end_date:
        raise ValueError(
            f"postseason game date is outside the configured window for season {policy.season}"
        )
    boundary = WEEK_ONE_BOUNDARIES[policy.season].date()
    return 1 + (local_day - boundary).days // 7

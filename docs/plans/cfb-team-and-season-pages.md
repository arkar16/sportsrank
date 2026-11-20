# College football team and season pages

## Team pages

The team directory opens each program’s history. Within a season, team links open that year’s team page whenever the archive has one. A compact line shows
season-ending national rank over time. A table gives the record, final national
rank, final CORS Rating and best rank reached for each completed season. For
the current season, label the latest figures with the week they cover. Opening
any season shows the team's weekly ranks, opponents, forecasts and scores.
Every program appearing in an archived CORS Ranking gets a permanent page,
including programs whose last ranked season was decades ago. Keep a documented
rename on the same program history and distinct schools separate. Game logs
also name opponents with no rankings of their own. The navigation reference is
the journey from [Oregon's school history](https://www.sports-reference.com/cfb/schools/oregon/)
through a season to its games.

## Season pages

Opening an active season shows the latest CORS Ranking and each team's change
in rank and CORS Rating since the previous week. Choosing an earlier week
answers the same questions as of that week. Results and upcoming forecasts are
one click away. Keep the existing links grouped by week in their established
order, including past forecasts. [Season navigation decision](https://github.com/arkar16/sportsrank/issues/50)

A completed season opens on final standings, movement from preseason to FINAL,
and the three largest qualifying upsets across the season. If preseason is
unavailable, identify the first available ranking used for comparison. Each
individual week's view keeps its own comparison and upset list.

For each week, show up to three CORS Upsets in which the winner was a CORS
underdog by at least seven points. Order them by the pregame deficit, largest
first. Show the CORS forecast and final score side by side. Use the underlying
Model Spread for qualification and order, including an exact seven-point line.
In 2026 Week 11, Georgia Tech's 20–6 win over Clemson qualifies at 15.09 points.
Washington State's 73–7 win over Colorado State falls below the cutoff at 3.18.

## School marks and navigation

Use the mark CFBD supplies for a program across its archive. Pull the team
catalog once through the metered Season Snapshot boundary. Download each
distinct selected image once, retain it as a shared static asset, and reuse it
across pages, seasons and offline rebuilds. A small mark accompanies the team
name on ranking and matchup rows. The team page gets a larger mark. A dense
week-by-week table shows the mark once per team. The written team name works
on its own wherever the API has no usable mark.

The [presentation decision](../adr/0007-use-a-statistical-reference-presentation.md)
defines the visual direction across the CFB site. Preserve existing public
URLs and core content in static HTML. The [glossary](../../CONTEXT.md) defines
Rank Movement, CORS Rating and CORS Upset. The [forecast decision](../adr/0019-evaluate-predictions-against-market-lines.md)
governs the forecasts and results available for comparison.

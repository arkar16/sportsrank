"""Cover historical CBBD scoring records using date, tournament and team-season endpoints."""

from collections import defaultdict
import heapq
from urllib.parse import urlencode

from .api_history import HOST


def cover_urls(games):
    candidates = defaultdict(set)
    for game in games:
        gid, year = game["id"], game["season"]
        day = game["startDate"][:10]
        candidates["date", day, ""].add(gid)
        for team in (game["homeTeam"], game["awayTeam"]):
            candidates["team", str(year), team].add(gid)
        if game.get("tournament"):
            candidates["tournament", str(year), game["tournament"]].add(gid)
    heap = [(-len(ids), key) for key, ids in candidates.items()]
    heapq.heapify(heap)
    covered = set()
    chosen = []
    while heap:
        negative, key = heapq.heappop(heap)
        new = candidates[key] - covered
        if not new:
            continue
        if len(new) != -negative:
            heapq.heappush(heap, (-len(new), key))
            continue
        chosen.append(key)
        covered.update(new)
    # Later team-season requests can also cover all games of an originally selected date.
    counts = defaultdict(int)
    for key in chosen:
        for gid in candidates[key]:
            counts[gid] += 1
    for key in reversed(tuple(chosen)):
        if all(counts[gid] > 1 for gid in candidates[key]):
            chosen.remove(key)
            for gid in candidates[key]:
                counts[gid] -= 1
    urls = []
    for kind, value, name in chosen:
        params = {"date": value} if kind == "date" else {"season": value, kind: name}
        params["shootingPlaysOnly"] = "true"
        urls.append(f"https://{HOST}/plays/{kind}?" + urlencode(params))
    return urls

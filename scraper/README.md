# iBIS historical scraper

Resumable collector for the public statistics on [stats.innebandy.se](https://stats.innebandy.se). It is meant for a private floorball stats app. Keep the database local and do not publish it until Svensk Innebandy has approved the use of the data.

The site is a JavaScript app. Player pages such as `https://stats.innebandy.se/spelare/114860` are rendered in the browser, and the page loads JSON from `https://api.innebandy.se/v2/api/public/`. That API is the same one the public site uses: an anonymous `startkit` token, a short-lived session cookie from `/v2/api/auth/handshake`, and an HMAC signature the frontend adds in the browser. The scraper uses that public path. It does not use an account, and it does not try to get past a login, captcha, or a response that refuses access.

Scraped databases and exports are gitignored under `data/` and `exports/`.

## Run

From this directory (`scraper/`):

```bash
python -m ibis_scraper scrape --player-id 114860 --player-id 426689 \
    --player-id 241859 --player-id 508042
python -m ibis_scraper validate --fixtures fixtures
```

One competition, with the match list, match events and lineups:

```bash
python -m ibis_scraper scrape --competition-id 45603 --matches
```

One season for one district federation:

```bash
python -m ibis_scraper scrape --season 2025/26 --federation-id 8 --matches
```

One club inside a season. The crawl still has to read that season's competition tables, and it keeps players, stats and matches tied to the association id:

```bash
python -m ibis_scraper scrape --season 2025/26 --association-id 100 --matches --with-profiles
```

Catalog only (seasons, federations, domestic competition lists, tournament category names, national-team competition lists):

```bash
python -m ibis_scraper scrape --full --lists-only
```

Full history, including matches, events, lineups and player pages. This is a long run. Stop it with Ctrl-C and start the same command again; finished URLs are skipped.

```bash
python -m ibis_scraper scrape --full --matches --with-profiles
```

`--full` already turns matches and player profiles on. `--lists-only` keeps it to catalog requests.

Other flags:

| Flag | Effect |
| --- | --- |
| `--db data/ibis.sqlite` | Staging database. Created if missing. |
| `--min-interval 1` | Minimum seconds between request starts. Default `1`. |
| `--refresh` | Fetch URLs that already succeeded. |
| `--match-id 1689669` | One match, including events. Add nothing else and it does not walk a competition. |
| `--sample-match-lists 30` | After the catalog is stored, download match lists for 30 competitions spread across ids. |
| `--no-events` / `--no-lineups` | With `--matches`, store the list and scores but skip detail pages or lineups. |
| `--tournaments` | Cup and tournament categories. Included in `--full`. |
| `--national-teams` | National-team competitions. Included in `--full`. |
| `--user-agent "..."` | Override the default identifiable user agent. |

Export and the size estimate:

```bash
python -m ibis_scraper export --out data/export
python -m ibis_scraper estimate
```

CSV writes an empty cell for NULL. JSON keeps `null`. Do not treat an empty CSV cell as zero.

Tests that do not touch the network:

```bash
python -m unittest discover -s tests
```

## Politeness and resume

- One API connection, requests issued one at a time.
- Default pace is at most one request per second (`--min-interval`). A `Crawl-delay` in robots.txt raises the interval when it is slower.
- `429` and `5xx` retry with exponential backoff and honour `Retry-After`.
- The run reads `https://stats.innebandy.se/robots.txt` first and stops if this user agent is disallowed. `User-agent: *` is `Allow: /`. Named crawlers such as GPTBot are `Disallow: /`; this scraper's user agent is not one of them. `api.innebandy.se/robots.txt` is not published (the host root returns 503 and `/v2/robots.txt` returns 404).
- User agent: `IbisHistoricalStatsScraper/0.1 (private historical collection; single connection; respects robots.txt)`.
- Each URL is stored in `http_checkpoint`. A later run skips `ok`, `absent` (404) and `forbidden` (401/403). It does not retry a forbidden URL until `--refresh`.
- Session tokens and the signing secret stay in memory. They are not written to the database or the repo.

## Schema

`schema.sql` is applied when the database opens. Tables are keyed by iBIS ids (`player_id`, `competition_id`, `match_id`, `match_event_id`, `license_id`, `season_id`, `federation_id`).

Important columns that are easy to misread:

- `players.summary_*` is the total printed on the player object. It is not recomputed from career rows. For player 114860 the object and the career table disagree; both are stored as the source sent them.
- `player_career_rows` is `GET /players/{id}/career`. The on-screen `TOTALT` lines are not in that payload. When `Competition` is blank and `CompetitionID` is 0, the public table shows `CompetitionCategory` instead (cups, qualifiers and some youth rows). Both fields are stored. `CompetitionID` 0 is stored as NULL.
- `competition_player_stats` is the competition's player table, including power-play and box-play columns when the source sends them.
- `label_kind` is a label classification (`regular`, `playoff`, `qualification`, `cup`, `youth`, `national_team`, `other`). It is derived from the competition name with the rules in `ibis_scraper/classify.py`. It is not an iBIS field. No matching token leaves it NULL.
- `PlayOffTypeName` on a competition is stored as `playoff_type_name`. A non-empty value means that competition has a playoff attached. It does not by itself mean the row is the playoff series.
- Match staff keeps name and role only. Phone numbers and email addresses from lineup payloads are dropped.
- `PlayerAssistID` `0` and blank assist names are stored as NULL. A real zero for goals, assists or penalty minutes is kept.
- `captured_at` is when this scraper stored the row.

## What the public API exposes

Collected when you ask for that scope:

- Seasons from 1986/87 onward, and the district federations plus Svenska IBF.
- Competitions for a federation and season: name, category, type id, status, promotion/relegation slots, and whether player or goalie stats are public.
- Tournament/cup categories, and with `--matches` the competitions, match lists and standings embedded in those category payloads.
- Player identity: iBIS player id, name, birth year, current club, parent club, and licence history (club, season, start and end). There is no full birth date on the player payload used here.
- Career rows: season, competition, team, matches, goals, assists, points, penalty minutes. The player page has no per-player match log; the API does not add one.
- Competition player stats and, when the competition marks them public, goalie shots, goals against and the source save percentage.
- Standings.
- Matches: date, teams, score, round, venue, status. Match detail adds spectators, referees, period shots and events (goals with scorer and assister, penalties, period markers, timeouts, goalkeeper changes).
- Lineups when the source sends them. A missing side stays absent rather than being filled in.
- National-team list and their competitions. Match detail for those games uses `nationalteams/matches/{id}` and is stored separately so those ids are not mixed with domestic match ids.

Not collected, or not available:

- No per-player match log from the player page.
- Player-object career totals are a different number from the sum of career rows. The scraper does not replace either one.
- Competitions with `PlayerStatisticsPublic: false` are not asked for player stats.
- Contacts (phone, email) are not stored.
- Scoring-pair tables, power-play team tables and live-match polling are public on the same API and are not part of this schema.
- `associations/{id}` returned 404 for a club id that appears on licences, so there is no club index endpoint to walk. Club scope is a filter on association ids found in stats and licences.
- A wide `federations/{id}/competitions/{from}/{to}` call returned an incomplete season set. The scraper asks for one season at a time.
- National-team event rows on a player career can have NULL goals, assists, points and penalty minutes. Those stay NULL. Matches are still present.

## Validation fixtures

`fixtures/` holds the four hand-collected careers (TOTALT lines included, and ignored by the checker):

| Player id | Name | Birth year |
| --- | --- | --- |
| 114860 | Victor Molén Nyqvist | 1988 |
| 426689 | Markus Falk | 1982 |
| 241859 | Emelie Wibron | 1992 |
| 508042 | My Kippilä | 1996 |

`python -m ibis_scraper validate` compares name, birth year and every non-TOTALT season row. For 114860 the fixture rows are 28 rows, 13 seasons, 240 matches and 255 points.

## Head-to-head

Match rows, lineups and events are the data behind "played against each other". Season totals cannot answer that. `sql/head_to_head.sql` has the queries. The view `player_head_to_head` keeps one row per match and unordered pair (lower player id is `player_a`). `relation` is `opponents` or `same_team`.

A player counts as participating only when a lineup row or an event names that player id. Sharing a club in a season table does not. Goals, assists and penalty minutes in the view come from events on a fetched match detail. When `detail_captured_at` is set and the goal events add up to the score, a missing goal or assist event is a verified zero. When the detail was not fetched, or the goal events do not add up to the score, those figures stay unknown. Penalty minutes are read from the penalty name (`2 min`, `2+2 min`). A match penalty with no stated length stays NULL.

Public match URL shape, from the site's routes:

`https://stats.innebandy.se/sasong/{season_id}/serie/{competition_id}/match/{match_id}`

One season of lineups and events:

```bash
python -m ibis_scraper coverage
```

That stores one played match per season already present in `competitions` and records whether the detail returned events and player ids.

Checked meetings, from lineups and events rather than season totals:

- 2011/12 Herrar Division 1 Norra Norrland, competition 25003. Sundsvall City IBC vs Ersboda SK was played twice. Both lineups name Victor Molén Nyqvist (114860) and Robin Jonsson (378994) on opposite sides. Goal events equal the score in both matches, and the event list includes other players' penalties, so a missing goal, assist or penalty for these two is a verified zero.
  - 2011-11-20, Sundsvall 11–10 Ersboda, match 935383. Victor, home, shirt 18: 0 goals, 2 assists (5:12 and 6:47), 0 penalty minutes. Robin, away, shirt 83: 0, 0, 0. https://stats.innebandy.se/sasong/29/serie/25003/match/935383
  - 2012-02-25, Ersboda 15–7 Sundsvall, match 935755. Victor, away, shirt 9, and Robin, home, shirt 83: 0 goals, 0 assists, 0 penalty minutes each. https://stats.innebandy.se/sasong/29/serie/25003/match/935755
- 2008/09 Herrar Division 1 Norra, competition 23297. Älvsjö AIK and Granlo BK met twice, and every match in that competition has a lineup. Markus Falk (426689) is in the Älvsjö lineup both times. Victor 114860 is in no lineup and no event in the competition, so he did not play in those meetings.
  - 2008-09-21, Älvsjö 11–3 Granlo, match 795576. Markus, home, shirt 8: 0 goals, 0 assists, 0 penalty minutes. https://stats.innebandy.se/sasong/26/serie/23297/match/795576
  - 2008-12-07, Granlo 1–6 Älvsjö, match 796525. Markus, away, shirt 8: 1 goal (18:00, assist Daniel Thorsson), 0 assists, 2 penalty minutes (förseelse med klubba at 10:23). https://stats.innebandy.se/sasong/26/serie/23297/match/796525
- 2026/27 Herrar Division 4 Södra, competition 44358. Two played matches list both on Högalids IF (A). Unplayed matches in that competition have no score and no lineup, so stats there are unknown.
  - 2026-10-18, Högalids 3–6 FBC Vega Haninge (A), match 1704024. Same team. Victor, shirt 7: 0 goals, 1 assist (7:27), 0 penalty minutes. Markus, shirt 18: 0, 0, 0. https://stats.innebandy.se/sasong/44/serie/44358/match/1704024
  - 2026-11-08, Högalids 3–2 Huddinge IBS (B), match 1704035. Same team. Victor shirt 7 and Markus shirt 18: 0 goals, 0 assists, 0 penalty minutes each. https://stats.innebandy.se/sasong/44/serie/44358/match/1704035

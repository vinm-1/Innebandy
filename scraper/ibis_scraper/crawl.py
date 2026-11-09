"""Resumable crawl planning. One request at a time, checkpointed by URL."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterable

from ibis_scraper.client import PUBLIC_PREFIX, IbisApiError, IbisClient
from ibis_scraper.db import (
    checkpoint_status,
    int_or_none,
    log_http,
    mark_checkpoint,
    replace_career_rows,
    replace_competition_player_stats,
    replace_goalie_stats,
    replace_lineup,
    replace_standings,
    upsert_competition,
    upsert_federation,
    upsert_match,
    upsert_national_competition,
    upsert_national_team,
    upsert_player,
    upsert_season,
    upsert_tournament_category,
)

log = logging.getLogger("ibis")

DONE = {"ok", "absent", "forbidden"}


@dataclass
class Scope:
    full: bool = False
    lists_only: bool = False
    season_ids: set[int] = field(default_factory=set)
    season_names: set[str] = field(default_factory=set)
    federation_ids: set[int] = field(default_factory=set)
    competition_ids: set[int] = field(default_factory=set)
    player_ids: set[int] = field(default_factory=set)
    match_ids: set[int] = field(default_factory=set)
    association_ids: set[int] = field(default_factory=set)
    matches: bool = False
    events: bool = True
    lineups: bool = True
    tournaments: bool = False
    national_teams: bool = False
    player_profiles: bool = False
    sample_match_lists: int = 0
    refresh: bool = False

    def wants_catalog(self) -> bool:
        return self.full or bool(self.season_ids or self.season_names or self.federation_ids or self.association_ids)

    def wants_competitions(self) -> bool:
        return self.wants_catalog() or bool(self.competition_ids)


class Crawler:
    def __init__(self, client: IbisClient, conn: sqlite3.Connection, scope: Scope) -> None:
        self.client = client
        self.conn = conn
        self.scope = scope
        self.requests = 0

    def run(self) -> None:
        self._fetch_seasons()
        self._fetch_federations()
        season_ids = self._selected_seasons()
        federation_ids = self._selected_federations()
        if self.scope.wants_catalog():
            for federation_id in federation_ids:
                for season_id in season_ids:
                    self._fetch_competition_list(federation_id, season_id)
                    if self.scope.tournaments or self.scope.full:
                        self._fetch_tournament_categories(federation_id, season_id)
        if self.scope.national_teams or self.scope.full:
            self._fetch_national_teams(with_matches=self.scope.matches and not self.scope.lists_only)
        competition_ids = self._competitions_to_expand()
        if not self.scope.lists_only:
            for competition_id in competition_ids:
                self._fetch_competition_details(competition_id)
        for player_id in sorted(self.scope.player_ids):
            self._fetch_player(player_id)
        for match_id in sorted(self.scope.match_ids):
            self._fetch_match_detail(match_id)
            if self.scope.lineups:
                self._fetch_lineup(match_id)
        if self.scope.sample_match_lists:
            self._sample_match_lists(self.scope.sample_match_lists)
        self.conn.commit()

    def _skip(self, path: str) -> bool:
        if self.scope.refresh:
            return False
        return checkpoint_status(self.conn, path) in DONE

    def _get(self, path: str) -> Any | None:
        if self._skip(path):
            log.info("skip %s", path)
            return None
        try:
            status, payload, result = self.client.get_json(path)
        except IbisApiError as exc:
            mark_checkpoint(self.conn, path, "error", exc.status, str(exc))
            log_http(self.conn, path, exc.status, None, None)
            self.conn.commit()
            raise
        self.requests += 1
        log_http(self.conn, path, status, result.elapsed_ms, len(result.body))
        if status == 404:
            mark_checkpoint(self.conn, path, "absent", status, None)
            self.conn.commit()
            log.info("absent %s", path)
            return None
        if status in (401, 403):
            mark_checkpoint(self.conn, path, "forbidden", status, None)
            self.conn.commit()
            log.warning("forbidden %s HTTP %s", path, status)
            return None
        if status != 200:
            mark_checkpoint(self.conn, path, "error", status, None)
            self.conn.commit()
            raise IbisApiError(f"unexpected HTTP {status} for {path}", status=status, path=path)
        return payload

    def _done(self, path: str, note: str | None = None) -> None:
        mark_checkpoint(self.conn, path, "ok", 200, note)
        self.conn.commit()

    def _fetch_seasons(self) -> None:
        path = f"{PUBLIC_PREFIX}/seasons"
        if self._skip(path):
            return
        payload = self._get(path)
        if not isinstance(payload, list):
            return
        for row in payload:
            if isinstance(row, dict) and row.get("SeasonID") is not None:
                upsert_season(self.conn, row)
        self._done(path)
        log.info("stored %s seasons", len(payload))

    def _fetch_federations(self) -> None:
        path = f"{PUBLIC_PREFIX}/federations"
        if self._skip(path):
            return
        payload = self._get(path)
        if not isinstance(payload, list):
            return
        for row in payload:
            if isinstance(row, dict) and row.get("FederationID") is not None:
                upsert_federation(self.conn, row)
        self._done(path)
        log.info("stored %s federations", len(payload))

    def _selected_seasons(self) -> list[int]:
        rows = self.conn.execute("SELECT season_id, name FROM seasons").fetchall()
        by_name = {str(row["name"]): int(row["season_id"]) for row in rows if row["name"]}
        chosen = set(self.scope.season_ids)
        for name in self.scope.season_names:
            if name not in by_name:
                known = ", ".join(sorted(by_name)[:8])
                raise SystemExit(f"unknown season {name!r}. Known names include: {known}")
            chosen.add(by_name[name])
        if self.scope.full or (self.scope.wants_catalog() and not chosen):
            chosen.update(int(row["season_id"]) for row in rows)
        return sorted(chosen)

    def _selected_federations(self) -> list[int]:
        rows = [int(row["federation_id"]) for row in self.conn.execute("SELECT federation_id FROM federations")]
        if self.scope.federation_ids:
            return sorted(self.scope.federation_ids)
        return sorted(rows)

    def _fetch_competition_list(self, federation_id: int, season_id: int) -> None:
        path = f"{PUBLIC_PREFIX}/federations/{federation_id}/competitions/{season_id}/{season_id}"
        if self._skip(path):
            return
        payload = self._get(path)
        if not isinstance(payload, list):
            return
        for row in payload:
            if isinstance(row, dict):
                upsert_competition(self.conn, row, federation_id=federation_id, source_path=path)
        self._done(path)
        log.info("federation %s season %s: %s competitions", federation_id, season_id, len(payload))

    def _fetch_tournament_categories(self, federation_id: int, season_id: int) -> None:
        path = f"{PUBLIC_PREFIX}/seasons/{season_id}/federations/{federation_id}/tournaments"
        if not self._skip(path):
            payload = self._get(path)
            if isinstance(payload, list):
                for row in payload:
                    if isinstance(row, dict):
                        upsert_tournament_category(self.conn, season_id, federation_id, row)
                self._done(path)
                log.info(
                    "federation %s season %s: %s tournament categories",
                    federation_id,
                    season_id,
                    len(payload),
                )
        if self.scope.lists_only or not self.scope.matches:
            return
        categories = self.conn.execute(
            """
            SELECT competition_category_id FROM tournament_categories
            WHERE season_id = ? AND federation_id = ?
            """,
            (season_id, federation_id),
        ).fetchall()
        for row in categories:
            self._fetch_tournament_bundle(season_id, federation_id, int(row["competition_category_id"]))

    def _fetch_tournament_bundle(self, season_id: int, federation_id: int, category_id: int) -> None:
        path = (
            f"{PUBLIC_PREFIX}/seasons/{season_id}/competitioncategories/"
            f"{category_id}/competitionswithmatchesandstandings"
        )
        if self._skip(path):
            return
        payload = self._get(path)
        if not isinstance(payload, list):
            return
        for row in payload:
            if not isinstance(row, dict):
                continue
            competition_id = upsert_competition(self.conn, row, federation_id=federation_id, source_path=path)
            if competition_id is None:
                continue
            if not self.scope.lists_only:
                self._fetch_competition_stats(competition_id, row)
            matches = [m for m in (row.get("Matches") or []) if isinstance(m, dict)]
            for match in self._filter_matches(matches, competition_id):
                upsert_match(self.conn, match, detailed=False)
            standings = row.get("Standings")
            if isinstance(standings, dict):
                replace_standings(self.conn, competition_id, standings)
                mark_checkpoint(
                    self.conn,
                    f"{PUBLIC_PREFIX}/competitions/{competition_id}/standings",
                    "ok",
                    200,
                    "embedded in tournament bundle",
                )
            list_path = f"{PUBLIC_PREFIX}/competitions/{competition_id}/matches"
            mark_checkpoint(self.conn, list_path, "ok", 200, "embedded in tournament bundle")
            if self.scope.matches and self.scope.events:
                for match in matches:
                    match_id = int_or_none(match.get("MatchID"))
                    if match_id is not None and self._match_in_scope(match_id, competition_id):
                        self._fetch_match_detail(match_id)
                        if self.scope.lineups:
                            self._fetch_lineup(match_id)
        self._done(path)

    def _competitions_to_expand(self) -> list[int]:
        chosen = set(self.scope.competition_ids)
        if self.scope.lists_only:
            return sorted(chosen)
        if self.scope.full and self.scope.matches:
            rows = self.conn.execute("SELECT competition_id FROM competitions").fetchall()
            chosen.update(int(row["competition_id"]) for row in rows)
        elif (self.scope.matches or self.scope.player_profiles or self.scope.association_ids) and self.scope.wants_catalog():
            query = "SELECT competition_id FROM competitions WHERE 1=1"
            params: list[Any] = []
            season_ids = self._selected_seasons()
            if season_ids and not self.scope.full:
                query += f" AND season_id IN ({','.join('?' for _ in season_ids)})"
                params.extend(season_ids)
            if self.scope.federation_ids:
                query += (
                    " AND competition_id IN (SELECT competition_id FROM competition_federations "
                    f"WHERE federation_id IN ({','.join('?' for _ in self.scope.federation_ids)}))"
                )
                params.extend(sorted(self.scope.federation_ids))
            rows = self.conn.execute(query, params).fetchall()
            chosen.update(int(row["competition_id"]) for row in rows)
        return sorted(chosen)

    def _fetch_competition_details(self, competition_id: int) -> None:
        path = f"{PUBLIC_PREFIX}/competitions/{competition_id}"
        if not self._skip(path):
            payload = self._get(path)
            if isinstance(payload, dict):
                upsert_competition(self.conn, payload, source_path=path)
                self._done(path)
        flags = self.conn.execute(
            """
            SELECT player_statistics_public, goalie_statistics_public
            FROM competitions WHERE competition_id = ?
            """,
            (competition_id,),
        ).fetchone()
        public_players = None if flags is None else flags["player_statistics_public"]
        public_goalies = None if flags is None else flags["goalie_statistics_public"]
        self._fetch_standings(competition_id)
        if public_players != 0:
            self._fetch_player_stats(competition_id)
        if public_goalies != 0:
            self._fetch_goalies(competition_id)
        if self.scope.matches:
            self._fetch_match_list(competition_id)

    def _fetch_competition_stats(self, competition_id: int, row: dict[str, Any]) -> None:
        if row.get("PlayerStatisticsPublic") is False:
            return
        self._fetch_player_stats(competition_id)
        if row.get("GoalieStatisticsPublic") is True:
            self._fetch_goalies(competition_id)

    def _fetch_standings(self, competition_id: int) -> None:
        path = f"{PUBLIC_PREFIX}/competitions/{competition_id}/standings"
        if self._skip(path):
            return
        payload = self._get(path)
        if isinstance(payload, dict):
            replace_standings(self.conn, competition_id, payload)
            self._done(path)

    def _fetch_player_stats(self, competition_id: int) -> None:
        path = f"{PUBLIC_PREFIX}/competitions/{competition_id}/playerstatistics"
        if self._skip(path):
            return
        payload = self._get(path)
        if not isinstance(payload, list):
            return
        association_filter = self.scope.association_ids or None
        replace_competition_player_stats(
            self.conn,
            competition_id,
            [row for row in payload if isinstance(row, dict)],
            association_filter,
        )
        self._done(path)
        if self.scope.player_profiles or self.scope.full:
            player_ids = [
                int(row["player_id"])
                for row in self.conn.execute(
                    "SELECT player_id FROM competition_player_stats WHERE competition_id = ?",
                    (competition_id,),
                )
            ]
            for player_id in player_ids:
                self._fetch_player(player_id)

    def _fetch_goalies(self, competition_id: int) -> None:
        path = f"{PUBLIC_PREFIX}/competitions/{competition_id}/goaliestatistics"
        if self._skip(path):
            return
        payload = self._get(path)
        if isinstance(payload, list):
            replace_goalie_stats(
                self.conn,
                competition_id,
                [row for row in payload if isinstance(row, dict)],
            )
            self._done(path)

    def _team_ids_for_association(self, competition_id: int) -> set[int] | None:
        if not self.scope.association_ids:
            return None
        rows = self.conn.execute(
            """
            SELECT DISTINCT team_id FROM competition_player_stats
            WHERE competition_id = ? AND association_id IN ({})
            """.format(",".join("?" for _ in self.scope.association_ids)),
            (competition_id, *sorted(self.scope.association_ids)),
        ).fetchall()
        return {int(row["team_id"]) for row in rows if row["team_id"] is not None}

    def _filter_matches(self, matches: Iterable[dict[str, Any]], competition_id: int) -> list[dict[str, Any]]:
        team_ids = self._team_ids_for_association(competition_id)
        kept = []
        for match in matches:
            if team_ids is None:
                kept.append(match)
                continue
            home = int_or_none(match.get("HomeTeamID"))
            away = int_or_none(match.get("AwayTeamID"))
            if (home is not None and home in team_ids) or (away is not None and away in team_ids):
                kept.append(match)
        return kept

    def _match_in_scope(self, match_id: int, competition_id: int) -> bool:
        if not self.scope.association_ids:
            return True
        row = self.conn.execute(
            "SELECT home_team_id, away_team_id FROM matches WHERE match_id = ?",
            (match_id,),
        ).fetchone()
        if row is None:
            return False
        team_ids = self._team_ids_for_association(competition_id)
        if not team_ids:
            return False
        return row["home_team_id"] in team_ids or row["away_team_id"] in team_ids

    def _fetch_match_list(self, competition_id: int) -> None:
        path = f"{PUBLIC_PREFIX}/competitions/{competition_id}/matches"
        if self._skip(path):
            match_rows = self.conn.execute(
                "SELECT match_id FROM matches WHERE competition_id = ?",
                (competition_id,),
            ).fetchall()
        else:
            payload = self._get(path)
            if not isinstance(payload, list):
                return
            matches = [row for row in payload if isinstance(row, dict)]
            for match in self._filter_matches(matches, competition_id):
                upsert_match(self.conn, match, detailed=False)
            self._done(path)
            log.info("competition %s: %s matches", competition_id, len(matches))
            match_rows = [
                {"match_id": int_or_none(match.get("MatchID"))}
                for match in self._filter_matches(matches, competition_id)
            ]
        if not self.scope.events and not self.scope.lineups:
            return
        for row in match_rows:
            match_id = int(row["match_id"]) if row["match_id"] is not None else None
            if match_id is None:
                continue
            if self.scope.events:
                self._fetch_match_detail(match_id)
            if self.scope.lineups:
                self._fetch_lineup(match_id)

    def _fetch_match_detail(self, match_id: int) -> None:
        path = f"{PUBLIC_PREFIX}/matches/{match_id}"
        if self._skip(path):
            return
        payload = self._get(path)
        if isinstance(payload, dict):
            upsert_match(self.conn, payload, detailed=True)
            self._done(path)

    def _fetch_lineup(self, match_id: int) -> None:
        path = f"{PUBLIC_PREFIX}/matches/{match_id}/lineups"
        if self._skip(path):
            return
        payload = self._get(path)
        if isinstance(payload, dict):
            try:
                replace_lineup(self.conn, match_id, payload)
            except sqlite3.Error as exc:
                log.warning("lineup %s was not stored: %s", match_id, exc)
                mark_checkpoint(self.conn, path, "error", 200, str(exc))
                self.conn.commit()
                return
            self._done(path)

    def _fetch_player(self, player_id: int) -> None:
        path = f"{PUBLIC_PREFIX}/players/{player_id}"
        if not self._skip(path):
            payload = self._get(path)
            if isinstance(payload, dict) and payload.get("PlayerID") is not None:
                upsert_player(self.conn, payload)
                self._done(path)
                log.info("player %s %s", player_id, payload.get("Name"))
        career_path = f"{PUBLIC_PREFIX}/players/{player_id}/career"
        if self._skip(career_path):
            return
        payload = self._get(career_path)
        rows: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            rows = [row for row in (payload.get("PlayerCareerRows") or []) if isinstance(row, dict)]
        elif isinstance(payload, list):
            rows = [row for row in payload if isinstance(row, dict)]
        else:
            return
        replace_career_rows(self.conn, player_id, rows)
        self._done(career_path)
        log.info("player %s career rows %s", player_id, len(rows))

    def _fetch_national_teams(self, with_matches: bool) -> None:
        path = f"{PUBLIC_PREFIX}/nationalteams"
        if not self._skip(path):
            payload = self._get(path)
            if isinstance(payload, list):
                for row in payload:
                    if isinstance(row, dict) and row.get("IffTeamID") is not None:
                        upsert_national_team(self.conn, row)
                self._done(path)
        teams = [
            int(row["iff_team_id"])
            for row in self.conn.execute("SELECT iff_team_id FROM national_teams")
        ]
        for team_id in teams:
            comp_path = f"{PUBLIC_PREFIX}/nationalteams/{team_id}/competitions"
            if not self._skip(comp_path):
                payload = self._get(comp_path)
                if isinstance(payload, list):
                    for row in payload:
                        if isinstance(row, dict) and row.get("IffCompetitionID") is not None:
                            upsert_national_competition(self.conn, team_id, row)
                    self._done(comp_path)
            if not with_matches:
                continue
            competitions = self.conn.execute(
                "SELECT iff_competition_id FROM national_competitions WHERE iff_team_id = ?",
                (team_id,),
            ).fetchall()
            for row in competitions:
                self._fetch_national_matches(int(row["iff_competition_id"]))

    def _fetch_national_matches(self, competition_id: int) -> None:
        path = f"{PUBLIC_PREFIX}/nationalteams/competitions/{competition_id}/matches"
        if self._skip(path):
            return
        payload = self._get(path)
        if not isinstance(payload, list):
            return
        for row in payload:
            if isinstance(row, dict):
                self._store_national_match(competition_id, row, detailed=False)
        self._done(path)
        if not self.scope.events:
            return
        for row in payload:
            if not isinstance(row, dict) or row.get("MatchID") is None:
                continue
            self._fetch_national_match_detail(int(row["MatchID"]))

    def _store_national_match(self, competition_id: int, row: dict[str, Any], *, detailed: bool) -> None:
        match_id = int_or_none(row.get("MatchID"))
        if match_id is None:
            return
        from ibis_scraper.db import blank_to_none, bool_or_none, utc_now

        captured = utc_now()
        self.conn.execute(
            """
            INSERT INTO national_matches (
                match_id, iff_competition_id, competition_name, match_datetime, match_no,
                match_status, home_team_id, home_team_name, away_team_id, away_team_name,
                goals_home, goals_away, has_final_result, venue, spectators,
                detail_captured_at, captured_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(match_id) DO UPDATE SET
                iff_competition_id = coalesce(excluded.iff_competition_id, national_matches.iff_competition_id),
                competition_name = coalesce(excluded.competition_name, national_matches.competition_name),
                match_datetime = coalesce(excluded.match_datetime, national_matches.match_datetime),
                goals_home = coalesce(excluded.goals_home, national_matches.goals_home),
                goals_away = coalesce(excluded.goals_away, national_matches.goals_away),
                spectators = coalesce(excluded.spectators, national_matches.spectators),
                detail_captured_at = coalesce(excluded.detail_captured_at, national_matches.detail_captured_at),
                captured_at = excluded.captured_at
            """,
            (
                match_id,
                int_or_none(row.get("IffCompetitionID")) or competition_id,
                blank_to_none(row.get("CompetitionName") or row.get("Name")),
                blank_to_none(row.get("MatchDateTime")),
                blank_to_none(row.get("MatchNo")),
                int_or_none(row.get("MatchStatus")),
                int_or_none(row.get("HomeTeamID")),
                blank_to_none(row.get("HomeTeam")),
                int_or_none(row.get("AwayTeamID")),
                blank_to_none(row.get("AwayTeam")),
                int_or_none(row.get("GoalsHomeTeam")),
                int_or_none(row.get("GoalsAwayTeam")),
                bool_or_none(row.get("HasFinalResult")),
                blank_to_none(row.get("Venue")),
                int_or_none(row.get("Spectators")),
                captured if detailed else None,
                captured,
            ),
        )
        if not detailed:
            return
        from ibis_scraper.db import player_id_or_none

        for event in row.get("Events") or []:
            if not isinstance(event, dict) or event.get("MatchEventID") is None:
                continue
            self.conn.execute(
                """
                INSERT INTO national_match_events (
                    match_event_id, match_id, event_type_id, event_type, period, minute, second,
                    player_id, player_name, assist_player_id, assist_player_name,
                    penalty_code, penalty_name, goals_home, goals_away, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(match_event_id) DO UPDATE SET
                    event_type = excluded.event_type,
                    player_id = excluded.player_id,
                    player_name = excluded.player_name,
                    assist_player_id = excluded.assist_player_id,
                    assist_player_name = excluded.assist_player_name,
                    penalty_code = excluded.penalty_code,
                    penalty_name = excluded.penalty_name,
                    captured_at = excluded.captured_at
                """,
                (
                    int(event["MatchEventID"]),
                    match_id,
                    int_or_none(event.get("MatchEventTypeID")),
                    blank_to_none(event.get("MatchEventType")),
                    int_or_none(event.get("Period")),
                    int_or_none(event.get("Minute")),
                    int_or_none(event.get("Second")),
                    player_id_or_none(event.get("PlayerID")),
                    blank_to_none(event.get("PlayerName")),
                    player_id_or_none(event.get("PlayerAssistID")),
                    blank_to_none(event.get("PlayerAssistName")),
                    blank_to_none(event.get("PenaltyCode")),
                    blank_to_none(event.get("PenaltyName")),
                    int_or_none(event.get("GoalsHomeTeam")),
                    int_or_none(event.get("GoalsAwayTeam")),
                    captured,
                ),
            )

    def _fetch_national_match_detail(self, match_id: int) -> None:
        path = f"{PUBLIC_PREFIX}/nationalteams/matches/{match_id}"
        if self._skip(path):
            return
        payload = self._get(path)
        if isinstance(payload, dict):
            competition_id = int_or_none(payload.get("IffCompetitionID")) or 0
            self._store_national_match(competition_id, payload, detailed=True)
            self._done(path)

    def _sample_match_lists(self, sample_size: int) -> None:
        rows = self.conn.execute(
            """
            SELECT c.competition_id
            FROM competitions c
            LEFT JOIN http_checkpoint h
              ON h.path = (? || c.competition_id || '/matches')
            WHERE h.status IS NULL OR h.status NOT IN ('ok', 'absent', 'forbidden')
            ORDER BY c.competition_id
            """,
            (f"{PUBLIC_PREFIX}/competitions/",),
        ).fetchall()
        ids = [int(row["competition_id"]) for row in rows]
        if not ids:
            log.info("no competitions left to sample")
            return
        if len(ids) > sample_size:
            step = len(ids) / sample_size
            ids = [ids[int(i * step)] for i in range(sample_size)]
        log.info("sampling match lists for %s competitions", len(ids))
        saved_matches = self.scope.matches
        saved_events = self.scope.events
        saved_lineups = self.scope.lineups
        self.scope.matches = True
        self.scope.events = False
        self.scope.lineups = False
        try:
            for competition_id in ids:
                self._fetch_match_list(competition_id)
        finally:
            self.scope.matches = saved_matches
            self.scope.events = saved_events
            self.scope.lineups = saved_lineups

    def sample_season_coverage(self) -> None:
        """Fetch one played match per stored season and record lineup/event availability."""
        seasons = self.conn.execute("SELECT season_id, name FROM seasons ORDER BY name").fetchall()
        for season in seasons:
            season_id = int(season["season_id"])
            competitions = self.conn.execute(
                """
                SELECT competition_id, name FROM competitions
                WHERE season_id = ? AND name IS NOT NULL
                ORDER BY
                    CASE
                        WHEN name LIKE '%Superligan%' OR name LIKE '%Elitserien%'
                             OR name LIKE 'SSL%' OR name LIKE '% SSL%' THEN 0
                        WHEN name LIKE '%Allsvenskan%' THEN 1
                        WHEN name LIKE '%Division 1%' OR name LIKE '%Div 1%' THEN 2
                        ELSE 3
                    END,
                    competition_id
                LIMIT 6
                """,
                (season_id,),
            ).fetchall()
            existing = self.conn.execute(
                "SELECT match_id, note FROM coverage_samples WHERE season_id = ?",
                (season_id,),
            ).fetchone()
            if existing is not None and not self.scope.refresh:
                placeholder = existing["match_id"] == 0 and existing["note"] == "no competitions stored"
                if not (placeholder and competitions):
                    continue
            if not competitions:
                self._record_coverage(season_id, season["name"], None, None, None, "no competitions stored")
                continue
            recorded = False
            for competition in competitions:
                competition_id = int(competition["competition_id"])
                match_id = self._one_played_match(competition_id)
                if match_id is None:
                    continue
                self._fetch_match_detail(match_id)
                self._fetch_lineup(match_id)
                self._record_coverage(
                    season_id,
                    season["name"],
                    competition_id,
                    competition["name"],
                    match_id,
                    None,
                )
                recorded = True
                break
            if not recorded:
                self._record_coverage(
                    season_id,
                    season["name"],
                    int(competitions[0]["competition_id"]),
                    competitions[0]["name"],
                    None,
                    "no played match in the sampled competitions",
                )
            log.info("coverage %s recorded=%s", season["name"], recorded)

    def _one_played_match(self, competition_id: int) -> int | None:
        path = f"{PUBLIC_PREFIX}/competitions/{competition_id}/matches"
        if not self._skip(path):
            payload = self._get(path)
            if isinstance(payload, list):
                for match in payload:
                    if isinstance(match, dict):
                        upsert_match(self.conn, match, detailed=False)
                self._done(path)
        row = self.conn.execute(
            """
            SELECT match_id FROM matches
            WHERE competition_id = ?
              AND (has_final_result = 1 OR match_status = 4)
              AND goals_home IS NOT NULL
              AND goals_away IS NOT NULL
            ORDER BY match_datetime
            LIMIT 1
            """,
            (competition_id,),
        ).fetchone()
        if row is None or row["match_id"] is None:
            return None
        return int(row["match_id"])

    def _record_coverage(
        self,
        season_id: int,
        season_name: str | None,
        competition_id: int | None,
        competition_name: str | None,
        match_id: int | None,
        note: str | None,
    ) -> None:
        from ibis_scraper.db import utc_now

        events = goal_events = penalty_events = lineup_players = lineup_ids = None
        match_datetime = None
        if match_id is not None:
            match_datetime = self.conn.execute(
                "SELECT match_datetime FROM matches WHERE match_id = ?",
                (match_id,),
            ).fetchone()
            match_datetime = None if match_datetime is None else match_datetime["match_datetime"]
            counts = self.conn.execute(
                """
                SELECT
                    (SELECT count(*) FROM match_events WHERE match_id = ?) AS events,
                    (SELECT count(*) FROM match_events WHERE match_id = ? AND event_type IN ('Mål', 'Straffmål')) AS goals,
                    (SELECT count(*) FROM match_events WHERE match_id = ? AND event_type = 'Utvisning') AS penalties,
                    (SELECT count(*) FROM match_lineups WHERE match_id = ?) AS lineup_players,
                    (SELECT count(*) FROM match_lineups WHERE match_id = ? AND player_id IS NOT NULL) AS lineup_ids
                """,
                (match_id, match_id, match_id, match_id, match_id),
            ).fetchone()
            events = counts["events"]
            goal_events = counts["goals"]
            penalty_events = counts["penalties"]
            lineup_players = counts["lineup_players"]
            lineup_ids = counts["lineup_ids"]
        key = match_id if match_id is not None else 0
        self.conn.execute(
            """
            INSERT INTO coverage_samples (
                season_id, season_name, competition_id, competition_name, match_id,
                match_datetime, events, goal_events, penalty_events, lineup_players,
                lineup_player_ids, note, captured_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(season_id, match_id) DO UPDATE SET
                season_name = excluded.season_name,
                competition_id = excluded.competition_id,
                competition_name = excluded.competition_name,
                match_datetime = excluded.match_datetime,
                events = excluded.events,
                goal_events = excluded.goal_events,
                penalty_events = excluded.penalty_events,
                lineup_players = excluded.lineup_players,
                lineup_player_ids = excluded.lineup_player_ids,
                note = excluded.note,
                captured_at = excluded.captured_at
            """,
            (
                season_id,
                season_name,
                competition_id,
                competition_name,
                key,
                match_datetime,
                events,
                goal_events,
                penalty_events,
                lineup_players,
                lineup_ids,
                note,
                utc_now(),
            ),
        )
        self.conn.commit()

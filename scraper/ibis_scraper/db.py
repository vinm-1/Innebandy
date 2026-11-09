"""SQLite staging store. Scraped files are local and gitignored."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def blank_to_none(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return value


def int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def player_id_or_none(value: Any) -> int | None:
    """iBIS uses 0 as 'no player' on events. That is not a player id."""
    number = int_or_none(value)
    if number is None or number == 0:
        return None
    return number


def real_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_MINUTE_PHRASE = re.compile(r"(\d+(?:\s*\+\s*\d+)*)\s*min\b", re.IGNORECASE)


def stated_penalty_minutes(name: str | None) -> int | None:
    """Read a penalty length only when the source name states minutes.

    "2 min" -> 2, "2+2 min" -> 4. A match penalty with no minute phrase stays None.
    """
    text = blank_to_none(name)
    if not isinstance(text, str):
        return None
    total = 0
    found = False
    for phrase in _MINUTE_PHRASE.findall(text):
        for part in re.split(r"\s*\+\s*", phrase):
            total += int(part)
            found = True
    return total if found else None


def bool_or_none(value: Any) -> int | None:
    if value is None:
        return None
    return 1 if bool(value) else 0


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    tables, marker, views = schema.partition("\n-- VIEWS\n")
    conn.executescript(tables)
    _migrate(conn)
    if marker:
        conn.executescript(views)
    # Expression indexes are not replaced by CREATE INDEX IF NOT EXISTS.
    conn.execute("DROP INDEX IF EXISTS ux_player_career_rows")
    conn.execute(
        """
        CREATE UNIQUE INDEX ux_player_career_rows ON player_career_rows (
            player_id,
            ifnull(season_id, -1),
            season_name,
            ifnull(competition_id, -1),
            ifnull(competition_name, ''),
            ifnull(competition_category_id, -1),
            ifnull(competition_category_name, ''),
            ifnull(team_id, -1),
            ifnull(team_name, '')
        )
        """
    )
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(match_events)")}
    if "penalty_minutes" not in columns:
        conn.execute("ALTER TABLE match_events ADD COLUMN penalty_minutes INTEGER")
    rows = conn.execute(
        """
        SELECT match_event_id, penalty_name
        FROM match_events
        WHERE penalty_name IS NOT NULL AND penalty_minutes IS NULL
        """
    ).fetchall()
    for row in rows:
        minutes = stated_penalty_minutes(row["penalty_name"])
        if minutes is not None:
            conn.execute(
                "UPDATE match_events SET penalty_minutes = ? WHERE match_event_id = ?",
                (minutes, row["match_event_id"]),
            )


def checkpoint_status(conn: sqlite3.Connection, path: str) -> str | None:
    row = conn.execute("SELECT status FROM http_checkpoint WHERE path = ?", (path,)).fetchone()
    return None if row is None else str(row["status"])


def mark_checkpoint(
    conn: sqlite3.Connection,
    path: str,
    status: str,
    http_status: int | None,
    note: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO http_checkpoint (path, status, http_status, fetched_at, note)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            status = excluded.status,
            http_status = excluded.http_status,
            fetched_at = excluded.fetched_at,
            note = excluded.note
        """,
        (path, status, http_status, utc_now(), note),
    )


def log_http(
    conn: sqlite3.Connection,
    path: str,
    http_status: int | None,
    elapsed_ms: int | None,
    nbytes: int | None,
) -> None:
    conn.execute(
        """
        INSERT INTO http_log (path, http_status, elapsed_ms, bytes, fetched_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (path, http_status, elapsed_ms, nbytes, utc_now()),
    )


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO meta (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def _replace(conn: sqlite3.Connection, sql: str, params: Iterable[Any]) -> None:
    conn.execute(sql, tuple(params))


def upsert_season(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    _replace(
        conn,
        """
        INSERT INTO seasons (season_id, name, is_current, captured_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(season_id) DO UPDATE SET
            name = excluded.name,
            is_current = excluded.is_current,
            captured_at = excluded.captured_at
        """,
        (
            int(row["SeasonID"]),
            blank_to_none(row.get("Name")),
            bool_or_none(row.get("IsCurrentSeason")),
            utc_now(),
        ),
    )


def upsert_federation(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    _replace(
        conn,
        """
        INSERT INTO federations (federation_id, name, captured_at)
        VALUES (?, ?, ?)
        ON CONFLICT(federation_id) DO UPDATE SET
            name = excluded.name,
            captured_at = excluded.captured_at
        """,
        (int(row["FederationID"]), blank_to_none(row.get("Name")), utc_now()),
    )


def upsert_association(
    conn: sqlite3.Connection,
    association_id: int | None,
    name: str | None,
    logotype_url: str | None = None,
) -> None:
    if association_id is None:
        return
    conn.execute(
        """
        INSERT INTO associations (association_id, name, logotype_url, captured_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(association_id) DO UPDATE SET
            name = coalesce(excluded.name, associations.name),
            logotype_url = coalesce(excluded.logotype_url, associations.logotype_url),
            captured_at = excluded.captured_at
        """,
        (association_id, blank_to_none(name), blank_to_none(logotype_url), utc_now()),
    )


def upsert_team(
    conn: sqlite3.Connection,
    team_id: int | None,
    name: str | None,
    short_name: str | None = None,
    association_id: int | None = None,
    logotype_url: str | None = None,
) -> None:
    if team_id is None:
        return
    conn.execute(
        """
        INSERT INTO teams (team_id, name, short_name, association_id, logotype_url, captured_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(team_id) DO UPDATE SET
            name = coalesce(excluded.name, teams.name),
            short_name = coalesce(excluded.short_name, teams.short_name),
            association_id = coalesce(excluded.association_id, teams.association_id),
            logotype_url = coalesce(excluded.logotype_url, teams.logotype_url),
            captured_at = excluded.captured_at
        """,
        (
            team_id,
            blank_to_none(name),
            blank_to_none(short_name),
            association_id,
            blank_to_none(logotype_url),
            utc_now(),
        ),
    )


def upsert_competition(
    conn: sqlite3.Connection,
    row: dict[str, Any],
    *,
    federation_id: int | None = None,
    source_path: str | None = None,
    national_team: bool = False,
) -> int | None:
    from ibis_scraper.classify import label_kind

    competition_id = int_or_none(row.get("CompetitionID"))
    if competition_id is None:
        return None
    name = blank_to_none(row.get("Name") or row.get("CompetitionName"))
    category = blank_to_none(row.get("CategoryName"))
    captured = utc_now()
    conn.execute(
        """
        INSERT INTO competitions (
            competition_id, season_id, season_name, name, category_id, category_name,
            competition_type_id, competition_status_id, playoff_category_id, playoff_type_name,
            max_matches_in_series, num_promoted_teams, num_demoted_teams,
            num_playoff_teams_up, num_playoff_teams_down,
            player_statistics_public, goalie_statistics_public, team_statistics_public,
            logotype_url, label_kind, source_path, captured_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(competition_id) DO UPDATE SET
            season_id = coalesce(excluded.season_id, competitions.season_id),
            season_name = coalesce(excluded.season_name, competitions.season_name),
            name = coalesce(excluded.name, competitions.name),
            category_id = coalesce(excluded.category_id, competitions.category_id),
            category_name = coalesce(excluded.category_name, competitions.category_name),
            competition_type_id = coalesce(excluded.competition_type_id, competitions.competition_type_id),
            competition_status_id = coalesce(excluded.competition_status_id, competitions.competition_status_id),
            playoff_category_id = coalesce(excluded.playoff_category_id, competitions.playoff_category_id),
            playoff_type_name = coalesce(excluded.playoff_type_name, competitions.playoff_type_name),
            max_matches_in_series = coalesce(excluded.max_matches_in_series, competitions.max_matches_in_series),
            num_promoted_teams = coalesce(excluded.num_promoted_teams, competitions.num_promoted_teams),
            num_demoted_teams = coalesce(excluded.num_demoted_teams, competitions.num_demoted_teams),
            num_playoff_teams_up = coalesce(excluded.num_playoff_teams_up, competitions.num_playoff_teams_up),
            num_playoff_teams_down = coalesce(excluded.num_playoff_teams_down, competitions.num_playoff_teams_down),
            player_statistics_public = coalesce(excluded.player_statistics_public, competitions.player_statistics_public),
            goalie_statistics_public = coalesce(excluded.goalie_statistics_public, competitions.goalie_statistics_public),
            team_statistics_public = coalesce(excluded.team_statistics_public, competitions.team_statistics_public),
            logotype_url = coalesce(excluded.logotype_url, competitions.logotype_url),
            label_kind = excluded.label_kind,
            source_path = coalesce(excluded.source_path, competitions.source_path),
            captured_at = excluded.captured_at
        """,
        (
            competition_id,
            int_or_none(row.get("SeasonID")),
            blank_to_none(row.get("SeasonName")),
            name,
            int_or_none(row.get("CategoryID")),
            category,
            int_or_none(row.get("CompetitionTypeID")),
            int_or_none(row.get("CompetitionStatusID")),
            int_or_none(row.get("PlayOffCategoryID")),
            blank_to_none(row.get("PlayOffTypeName")),
            int_or_none(row.get("MaxNoOfMatchesInMatchSeries")),
            int_or_none(row.get("NumPromotedTeams")),
            int_or_none(row.get("NumDemotedTeams")),
            int_or_none(row.get("NumPlayOffTeamsUp")),
            int_or_none(row.get("NumPlayOffTeamsDown")),
            bool_or_none(row.get("PlayerStatisticsPublic")),
            bool_or_none(row.get("GoalieStatisticsPublic")),
            bool_or_none(row.get("TeamStatisticsPublic")),
            blank_to_none(row.get("LogotypeUrl")),
            label_kind(name, category, national_team=national_team),
            source_path,
            captured,
        ),
    )
    if federation_id is not None:
        conn.execute(
            """
            INSERT OR IGNORE INTO competition_federations (competition_id, federation_id)
            VALUES (?, ?)
            """,
            (competition_id, federation_id),
        )
    return competition_id


def upsert_player(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    player_id = int(row["PlayerID"])
    upsert_association(
        conn,
        int_or_none(row.get("LicensedAssociationID")),
        blank_to_none(row.get("LicensedAssociationName")),
    )
    upsert_association(
        conn,
        int_or_none(row.get("AssociationOfOriginID")),
        blank_to_none(row.get("AssociationOfOriginName")),
    )
    conn.execute(
        """
        INSERT INTO players (
            player_id, person_id, name, birth_year, age_reported, shirt_no, position_id, position,
            licensed_association_id, licensed_association_name, origin_association_id, origin_association_name,
            summary_matches, summary_goals, summary_assists, summary_points, summary_penalty_minutes,
            source_url, captured_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(player_id) DO UPDATE SET
            person_id = excluded.person_id,
            name = excluded.name,
            birth_year = excluded.birth_year,
            age_reported = excluded.age_reported,
            shirt_no = excluded.shirt_no,
            position_id = excluded.position_id,
            position = excluded.position,
            licensed_association_id = excluded.licensed_association_id,
            licensed_association_name = excluded.licensed_association_name,
            origin_association_id = excluded.origin_association_id,
            origin_association_name = excluded.origin_association_name,
            summary_matches = excluded.summary_matches,
            summary_goals = excluded.summary_goals,
            summary_assists = excluded.summary_assists,
            summary_points = excluded.summary_points,
            summary_penalty_minutes = excluded.summary_penalty_minutes,
            source_url = excluded.source_url,
            captured_at = excluded.captured_at
        """,
        (
            player_id,
            int_or_none(row.get("PersonID")),
            blank_to_none(row.get("Name")),
            int_or_none(row.get("BirthYear")),
            int_or_none(row.get("Age")),
            int_or_none(row.get("ShirtNo")),
            int_or_none(row.get("PositionID")),
            blank_to_none(row.get("Position")),
            int_or_none(row.get("LicensedAssociationID")),
            blank_to_none(row.get("LicensedAssociationName")),
            int_or_none(row.get("AssociationOfOriginID")),
            blank_to_none(row.get("AssociationOfOriginName")),
            int_or_none(row.get("Matches")),
            int_or_none(row.get("Goals")),
            int_or_none(row.get("Assists")),
            int_or_none(row.get("Points")),
            int_or_none(row.get("PenaltyMinutes")),
            f"https://stats.innebandy.se/spelare/{player_id}",
            utc_now(),
        ),
    )
    for license_row in row.get("LicenseHistory") or []:
        if not isinstance(license_row, dict) or license_row.get("LicenseID") is None:
            continue
        association_id = int_or_none(license_row.get("AssociationID"))
        upsert_association(
            conn,
            association_id,
            blank_to_none(license_row.get("AssociationName")),
            blank_to_none(license_row.get("AssociationLogotypeUrl")),
        )
        conn.execute(
            """
            INSERT INTO license_history (
                license_id, player_id, season_id, season_name, association_id, association_name,
                start_date, end_date, license_type_id, license_type_name, captured_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(license_id) DO UPDATE SET
                player_id = excluded.player_id,
                season_id = excluded.season_id,
                season_name = excluded.season_name,
                association_id = excluded.association_id,
                association_name = excluded.association_name,
                start_date = excluded.start_date,
                end_date = excluded.end_date,
                license_type_id = excluded.license_type_id,
                license_type_name = excluded.license_type_name,
                captured_at = excluded.captured_at
            """,
            (
                int(license_row["LicenseID"]),
                player_id,
                int_or_none(license_row.get("SeasonID")),
                blank_to_none(license_row.get("SeasonName")),
                association_id,
                blank_to_none(license_row.get("AssociationName")),
                blank_to_none(license_row.get("StartDate")),
                blank_to_none(license_row.get("EndDate")),
                int_or_none(license_row.get("LicenseTypeID")),
                blank_to_none(license_row.get("LicenseTypeName")),
                utc_now(),
            ),
        )


def replace_career_rows(conn: sqlite3.Connection, player_id: int, rows: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM player_career_rows WHERE player_id = ?", (player_id,))
    captured = utc_now()
    for row in rows:
        season_name = blank_to_none(row.get("Season"))
        competition_name = blank_to_none(row.get("Competition"))
        category_name = blank_to_none(row.get("CompetitionCategory"))
        # The public career table shows Competition, and uses the category
        # label when Competition is blank and CompetitionID is 0.
        if competition_name is not None and competition_name.casefold() == "totalt":
            continue
        if season_name is None or (competition_name is None and category_name is None):
            continue
        team_id = int_or_none(row.get("TeamID"))
        team_name = blank_to_none(row.get("Team"))
        upsert_team(conn, team_id, team_name)
        competition_id = int_or_none(row.get("CompetitionID"))
        if competition_id == 0:
            competition_id = None
        if competition_id is not None:
            upsert_competition(
                conn,
                {
                    "CompetitionID": competition_id,
                    "SeasonID": row.get("SeasonID"),
                    "SeasonName": season_name,
                    "Name": competition_name,
                    "CategoryID": row.get("CompetitionCategoryID"),
                    "CategoryName": row.get("CompetitionCategory"),
                },
                source_path=f"/v2/api/public/players/{player_id}/career",
            )
        conn.execute(
            """
            INSERT INTO player_career_rows (
                player_id, season_id, season_name, competition_id, competition_name,
                competition_category_id, competition_category_name, team_id, team_name,
                matches, goals, assists, points, penalty_minutes, current_season, captured_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                player_id,
                int_or_none(row.get("SeasonID")),
                season_name,
                competition_id,
                competition_name,
                int_or_none(row.get("CompetitionCategoryID")),
                blank_to_none(row.get("CompetitionCategory")),
                team_id,
                team_name,
                int_or_none(row.get("Matches")),
                int_or_none(row.get("Goals")),
                int_or_none(row.get("Assists")),
                int_or_none(row.get("Points")),
                int_or_none(row.get("PenaltyMinutes")),
                bool_or_none(row.get("CurrentSeason")),
                captured,
            ),
        )


def replace_competition_player_stats(
    conn: sqlite3.Connection,
    competition_id: int,
    rows: list[dict[str, Any]],
    association_ids: set[int] | None = None,
) -> set[int]:
    """Store player-stat rows. Returns team ids kept (after an optional association filter)."""
    conn.execute(
        "DELETE FROM competition_player_stats WHERE competition_id = ?",
        (competition_id,),
    )
    kept_teams: set[int] = set()
    captured = utc_now()
    for row in rows:
        player_id = player_id_or_none(row.get("PlayerID"))
        if player_id is None:
            continue
        association_id = int_or_none(row.get("AssociationID"))
        if association_ids is not None and association_id not in association_ids:
            continue
        team_id = int_or_none(row.get("TeamID"))
        if team_id is not None:
            kept_teams.add(team_id)
        upsert_association(conn, association_id, blank_to_none(row.get("AssociationName")))
        upsert_team(conn, team_id, blank_to_none(row.get("TeamName")), association_id=association_id)
        conn.execute(
            """
            INSERT INTO competition_player_stats (
                competition_id, player_id, team_id, player_name, team_name,
                association_id, association_name, matches, goals, assists, points,
                penalty_minutes, goals_pp, goals_bp, assists_pp, assists_bp, points_pp, points_bp,
                captured_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                competition_id,
                player_id,
                team_id,
                blank_to_none(row.get("PlayerName")),
                blank_to_none(row.get("TeamName")),
                association_id,
                blank_to_none(row.get("AssociationName")),
                int_or_none(row.get("MatchesPlayed")),
                int_or_none(row.get("GoalsScored")),
                int_or_none(row.get("Assists")),
                int_or_none(row.get("Points")),
                int_or_none(row.get("PenaltyMinutes")),
                int_or_none(row.get("GoalsScoredPp")),
                int_or_none(row.get("GoalsScoredBp")),
                int_or_none(row.get("AssistsPp")),
                int_or_none(row.get("AssistsBp")),
                int_or_none(row.get("PointsPp")),
                int_or_none(row.get("PointsBp")),
                captured,
            ),
        )
    return kept_teams


def replace_goalie_stats(conn: sqlite3.Connection, competition_id: int, rows: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM goalie_stats WHERE competition_id = ?", (competition_id,))
    captured = utc_now()
    for row in rows:
        player_id = player_id_or_none(row.get("PlayerID"))
        if player_id is None:
            continue
        team_id = int_or_none(row.get("TeamID"))
        association_id = int_or_none(row.get("AssociationID"))
        upsert_association(conn, association_id, blank_to_none(row.get("AssociationName")))
        upsert_team(
            conn,
            team_id,
            blank_to_none(row.get("TeamName")),
            short_name=blank_to_none(row.get("TeamShortName")),
            association_id=association_id,
        )
        conn.execute(
            """
            INSERT INTO goalie_stats (
                competition_id, player_id, team_id, player_name, team_name, team_short_name,
                association_id, association_name, matches, shots_against, goals_against,
                save_percentage, captured_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                competition_id,
                player_id,
                team_id,
                blank_to_none(row.get("PlayerName")),
                blank_to_none(row.get("TeamName")),
                blank_to_none(row.get("TeamShortName")),
                association_id,
                blank_to_none(row.get("AssociationName")),
                int_or_none(row.get("MatchesPlayed")),
                int_or_none(row.get("ShotsAgainst")),
                int_or_none(row.get("GoalsAgainst")),
                real_or_none(row.get("SavePercentage")),
                captured,
            ),
        )


def replace_standings(conn: sqlite3.Connection, competition_id: int, payload: dict[str, Any]) -> None:
    conn.execute("DELETE FROM standings WHERE competition_id = ?", (competition_id,))
    captured = utc_now()
    for row in payload.get("StandingsRows") or []:
        if not isinstance(row, dict) or row.get("TeamID") is None:
            continue
        team_id = int(row["TeamID"])
        upsert_team(
            conn,
            team_id,
            blank_to_none(row.get("TeamName")),
            logotype_url=blank_to_none(row.get("TeamLogotypeUrl")),
        )
        last_games = row.get("LastGames")
        last_json = None if last_games is None else json.dumps(last_games, ensure_ascii=False)
        conn.execute(
            """
            INSERT INTO standings (
                competition_id, team_id, team_name, team_status_id, position,
                played_home, wins_home, draws_home, losses_home, sudden_death_wins_home,
                goals_for_home, goals_against_home, played_away, wins_away, draws_away,
                losses_away, sudden_death_wins_away, goals_for_away, goals_against_away,
                scoring_diff, points, last_games_json, captured_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                competition_id,
                team_id,
                blank_to_none(row.get("TeamName")),
                int_or_none(row.get("TeamStatusID")),
                int_or_none(row.get("Position")),
                int_or_none(row.get("PlayedMatchesHome")),
                int_or_none(row.get("WinsHome")),
                int_or_none(row.get("DrawsHome")),
                int_or_none(row.get("LossesHome")),
                int_or_none(row.get("SdWinsHome")),
                int_or_none(row.get("GoalsScoredHome")),
                int_or_none(row.get("GoalsAgainstHome")),
                int_or_none(row.get("PlayedMatchesAway")),
                int_or_none(row.get("WinsAway")),
                int_or_none(row.get("DrawsAway")),
                int_or_none(row.get("LossesAway")),
                int_or_none(row.get("SdWinsAway")),
                int_or_none(row.get("GoalsScoredAway")),
                int_or_none(row.get("GoalsAgainstAway")),
                int_or_none(row.get("ScoringDiff")),
                int_or_none(row.get("Points")),
                last_json,
                captured,
            ),
        )


def upsert_match(conn: sqlite3.Connection, row: dict[str, Any], *, detailed: bool = False) -> int | None:
    match_id = int_or_none(row.get("MatchID"))
    if match_id is None:
        return None
    home_id = int_or_none(row.get("HomeTeamID"))
    away_id = int_or_none(row.get("AwayTeamID"))
    upsert_team(
        conn,
        home_id,
        blank_to_none(row.get("HomeTeam")),
        short_name=blank_to_none(row.get("HomeTeamShortName")),
        logotype_url=blank_to_none(row.get("HomeTeamLogotypeUrl")),
    )
    upsert_team(
        conn,
        away_id,
        blank_to_none(row.get("AwayTeam")),
        short_name=blank_to_none(row.get("AwayTeamShortName")),
        logotype_url=blank_to_none(row.get("AwayTeamLogotypeUrl")),
    )
    captured = utc_now()
    existing = conn.execute("SELECT match_id FROM matches WHERE match_id = ?", (match_id,)).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO matches (
                match_id, competition_id, competition_name, season_id, match_datetime, match_no,
                match_status, home_team_id, home_team_name, home_team_short_name,
                away_team_id, away_team_name, away_team_short_name, goals_home, goals_away,
                has_final_result, match_result_type_id, cancelled, postponed, abandoned, note,
                round, round_name, venue_id, venue, main_venue_id, main_venue,
                arranging_association_id, arranging_association, spectators,
                home_match_team_id, away_match_team_id, referee1_id, referee1_name,
                referee2_id, referee2_name, home_shirt_color, away_shirt_color,
                detail_captured_at, captured_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _match_params(row, match_id, captured, detailed),
        )
    else:
        conn.execute(
            """
            UPDATE matches SET
                competition_id = coalesce(?, competition_id),
                competition_name = coalesce(?, competition_name),
                season_id = coalesce(?, season_id),
                match_datetime = coalesce(?, match_datetime),
                match_no = coalesce(?, match_no),
                match_status = coalesce(?, match_status),
                home_team_id = coalesce(?, home_team_id),
                home_team_name = coalesce(?, home_team_name),
                home_team_short_name = coalesce(?, home_team_short_name),
                away_team_id = coalesce(?, away_team_id),
                away_team_name = coalesce(?, away_team_name),
                away_team_short_name = coalesce(?, away_team_short_name),
                goals_home = coalesce(?, goals_home),
                goals_away = coalesce(?, goals_away),
                has_final_result = coalesce(?, has_final_result),
                match_result_type_id = coalesce(?, match_result_type_id),
                cancelled = coalesce(?, cancelled),
                postponed = coalesce(?, postponed),
                abandoned = coalesce(?, abandoned),
                note = coalesce(?, note),
                round = coalesce(?, round),
                round_name = coalesce(?, round_name),
                venue_id = coalesce(?, venue_id),
                venue = coalesce(?, venue),
                main_venue_id = coalesce(?, main_venue_id),
                main_venue = coalesce(?, main_venue),
                arranging_association_id = coalesce(?, arranging_association_id),
                arranging_association = coalesce(?, arranging_association),
                spectators = coalesce(?, spectators),
                home_match_team_id = coalesce(?, home_match_team_id),
                away_match_team_id = coalesce(?, away_match_team_id),
                referee1_id = coalesce(?, referee1_id),
                referee1_name = coalesce(?, referee1_name),
                referee2_id = coalesce(?, referee2_id),
                referee2_name = coalesce(?, referee2_name),
                home_shirt_color = coalesce(?, home_shirt_color),
                away_shirt_color = coalesce(?, away_shirt_color),
                detail_captured_at = coalesce(?, detail_captured_at),
                captured_at = ?
            WHERE match_id = ?
            """,
            _match_update_params(row, captured, detailed) + (match_id,),
        )
    if detailed:
        _replace_match_children(conn, match_id, row, captured)
    return match_id


def _match_params(row: dict[str, Any], match_id: int, captured: str, detailed: bool) -> tuple[Any, ...]:
    return (
        match_id,
        int_or_none(row.get("CompetitionID")),
        blank_to_none(row.get("CompetitionName")),
        int_or_none(row.get("SeasonID")),
        blank_to_none(row.get("MatchDateTime")),
        blank_to_none(row.get("MatchNo")),
        int_or_none(row.get("MatchStatus")),
        int_or_none(row.get("HomeTeamID")),
        blank_to_none(row.get("HomeTeam")),
        blank_to_none(row.get("HomeTeamShortName")),
        int_or_none(row.get("AwayTeamID")),
        blank_to_none(row.get("AwayTeam")),
        blank_to_none(row.get("AwayTeamShortName")),
        int_or_none(row.get("GoalsHomeTeam")),
        int_or_none(row.get("GoalsAwayTeam")),
        bool_or_none(row.get("HasFinalResult")),
        int_or_none(row.get("MatchResultTypeID")),
        bool_or_none(row.get("Cancelled")),
        bool_or_none(row.get("Postponed")),
        bool_or_none(row.get("Abandoned")),
        blank_to_none(row.get("Note")),
        int_or_none(row.get("Round")),
        blank_to_none(row.get("RoundName")),
        int_or_none(row.get("VenueID")),
        blank_to_none(row.get("Venue")),
        int_or_none(row.get("MainVenueID")),
        blank_to_none(row.get("MainVenue")),
        int_or_none(row.get("ArrangingAssociationID")),
        blank_to_none(row.get("ArrangingAssociation")),
        int_or_none(row.get("Spectators")),
        int_or_none(row.get("HomeMatchTeamID")),
        int_or_none(row.get("AwayMatchTeamID")),
        player_id_or_none(row.get("Referee1ID")),
        blank_to_none(row.get("Referee1")),
        player_id_or_none(row.get("Referee2ID")),
        blank_to_none(row.get("Referee2")),
        blank_to_none(row.get("HomeTeamShirtColor")),
        blank_to_none(row.get("AwayTeamShirtColor")),
        captured if detailed else None,
        captured,
    )


def _match_update_params(row: dict[str, Any], captured: str, detailed: bool) -> tuple[Any, ...]:
    params = _match_params(row, 0, captured, detailed)
    # Drop match_id (first) and captured_at (last); detail flag and captured_at are applied by the SQL.
    return params[1:-1] + (captured,)


def _replace_match_children(conn: sqlite3.Connection, match_id: int, row: dict[str, Any], captured: str) -> None:
    conn.execute("DELETE FROM match_period_shots WHERE match_id = ?", (match_id,))
    conn.execute("DELETE FROM match_events WHERE match_id = ?", (match_id,))
    for shot in row.get("ShotsOnGoal") or []:
        if not isinstance(shot, dict) or shot.get("Period") is None:
            continue
        conn.execute(
            """
            INSERT INTO match_period_shots (match_id, period, shots_home, shots_away, captured_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                match_id,
                int(shot["Period"]),
                int_or_none(shot.get("ShotsHomeTeam")),
                int_or_none(shot.get("ShotsAwayTeam")),
                captured,
            ),
        )
    for event in row.get("Events") or []:
        if not isinstance(event, dict) or event.get("MatchEventID") is None:
            continue
        conn.execute(
            """
            INSERT INTO match_events (
                match_event_id, match_id, event_type_id, event_type, match_team_id, period,
                period_name, minute, second, player_id, player_shirt_no, player_name,
                assist_player_id, assist_shirt_no, assist_player_name, penalty_code, penalty_name,
                penalty_minutes, goals_home, goals_away, captured_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(match_event_id) DO UPDATE SET
                match_id = excluded.match_id,
                event_type_id = excluded.event_type_id,
                event_type = excluded.event_type,
                match_team_id = excluded.match_team_id,
                period = excluded.period,
                period_name = excluded.period_name,
                minute = excluded.minute,
                second = excluded.second,
                player_id = excluded.player_id,
                player_shirt_no = excluded.player_shirt_no,
                player_name = excluded.player_name,
                assist_player_id = excluded.assist_player_id,
                assist_shirt_no = excluded.assist_shirt_no,
                assist_player_name = excluded.assist_player_name,
                penalty_code = excluded.penalty_code,
                penalty_name = excluded.penalty_name,
                penalty_minutes = excluded.penalty_minutes,
                goals_home = excluded.goals_home,
                goals_away = excluded.goals_away,
                captured_at = excluded.captured_at
            """,
            (
                int(event["MatchEventID"]),
                match_id,
                int_or_none(event.get("MatchEventTypeID")),
                blank_to_none(event.get("MatchEventType")),
                int_or_none(event.get("MatchTeamID")),
                int_or_none(event.get("Period")),
                blank_to_none(event.get("PeriodName")),
                int_or_none(event.get("Minute")),
                int_or_none(event.get("Second")),
                player_id_or_none(event.get("PlayerID")),
                int_or_none(event.get("PlayerShirtNo")),
                blank_to_none(event.get("PlayerName")),
                player_id_or_none(event.get("PlayerAssistID")),
                int_or_none(event.get("PlayerAssistShirtNo")),
                blank_to_none(event.get("PlayerAssistName")),
                blank_to_none(event.get("PenaltyCode")),
                blank_to_none(event.get("PenaltyName")),
                stated_penalty_minutes(event.get("PenaltyName")),
                int_or_none(event.get("GoalsHomeTeam")),
                int_or_none(event.get("GoalsAwayTeam")),
                captured,
            ),
        )


def replace_lineup(conn: sqlite3.Connection, match_id: int, payload: dict[str, Any]) -> None:
    conn.execute("DELETE FROM match_lineups WHERE match_id = ?", (match_id,))
    conn.execute("DELETE FROM match_staff WHERE match_id = ?", (match_id,))
    captured = utc_now()
    for side, players_key, staff_key in (
        ("home", "HomeTeamPlayers", "HomeTeamTeamPersons"),
        ("away", "AwayTeamPlayers", "AwayTeamTeamPersons"),
    ):
        for player in payload.get(players_key) or []:
            if not isinstance(player, dict):
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO match_lineups (
                    match_id, side, player_id, match_player_id, name, shirt_no, captain, line,
                    position_id, position, shots_on_goal, goals_against, save_percentage, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match_id,
                    side,
                    player_id_or_none(player.get("PlayerID")),
                    int_or_none(player.get("MatchPlayerID")),
                    blank_to_none(player.get("Name")),
                    int_or_none(player.get("ShirtNo")),
                    bool_or_none(player.get("Captain")),
                    int_or_none(player.get("Line")),
                    int_or_none(player.get("PositionID")),
                    blank_to_none(player.get("Position")),
                    int_or_none(player.get("ShotsOnGoal")),
                    int_or_none(player.get("GoalsAgainst")),
                    real_or_none(player.get("SavePercentage")),
                    captured,
                ),
            )
        for person in payload.get(staff_key) or []:
            if not isinstance(person, dict):
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO match_staff (
                    match_id, side, person_id, name, role_id, role_name, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match_id,
                    side,
                    player_id_or_none(person.get("PersonID")),
                    blank_to_none(person.get("Name")),
                    int_or_none(person.get("RoleID")),
                    blank_to_none(person.get("RoleName")),
                    captured,
                ),
            )


def upsert_tournament_category(
    conn: sqlite3.Connection,
    season_id: int,
    federation_id: int,
    row: dict[str, Any],
) -> None:
    category_id = int_or_none(row.get("CompetitionCategoryID"))
    if category_id is None:
        return
    conn.execute(
        """
        INSERT INTO tournament_categories (
            season_id, federation_id, competition_category_id, name, captured_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(season_id, federation_id, competition_category_id) DO UPDATE SET
            name = excluded.name,
            captured_at = excluded.captured_at
        """,
        (season_id, federation_id, category_id, blank_to_none(row.get("Name")), utc_now()),
    )


def upsert_national_team(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO national_teams (iff_team_id, name, short_name, logotype_url, captured_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(iff_team_id) DO UPDATE SET
            name = excluded.name,
            short_name = excluded.short_name,
            logotype_url = excluded.logotype_url,
            captured_at = excluded.captured_at
        """,
        (
            int(row["IffTeamID"]),
            blank_to_none(row.get("Name")),
            blank_to_none(row.get("ShortName")),
            blank_to_none(row.get("LogotypeUrl")),
            utc_now(),
        ),
    )


def upsert_national_competition(conn: sqlite3.Connection, team_id: int, row: dict[str, Any]) -> None:
    from ibis_scraper.classify import label_kind

    conn.execute(
        """
        INSERT INTO national_competitions (
            iff_competition_id, iff_team_id, name, year, start_date, label_kind, captured_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(iff_competition_id) DO UPDATE SET
            iff_team_id = excluded.iff_team_id,
            name = excluded.name,
            year = excluded.year,
            start_date = excluded.start_date,
            label_kind = excluded.label_kind,
            captured_at = excluded.captured_at
        """,
        (
            int(row["IffCompetitionID"]),
            team_id,
            blank_to_none(row.get("Name")),
            int_or_none(row.get("Year")),
            blank_to_none(row.get("StartDate")),
            label_kind(blank_to_none(row.get("Name")), national_team=True),
            utc_now(),
        ),
    )


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = [
        "seasons",
        "federations",
        "competitions",
        "tournament_categories",
        "associations",
        "teams",
        "players",
        "license_history",
        "player_career_rows",
        "competition_player_stats",
        "goalie_stats",
        "standings",
        "matches",
        "match_events",
        "match_lineups",
        "match_staff",
        "national_teams",
        "national_competitions",
        "http_checkpoint",
        "http_log",
    ]
    out = {}
    for table in tables:
        out[table] = int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
    return out

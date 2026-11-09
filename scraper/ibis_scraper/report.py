"""Counts and a rate-based estimate of a full historical run."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from ibis_scraper.db import counts


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def measured_seconds_per_request(conn: sqlite3.Connection) -> float | None:
    rows = conn.execute(
        "SELECT fetched_at FROM http_log ORDER BY id"
    ).fetchall()
    stamps = []
    for row in rows:
        try:
            stamps.append(datetime.fromisoformat(row["fetched_at"]))
        except ValueError:
            continue
    gaps = [(b - a).total_seconds() for a, b in zip(stamps, stamps[1:]) if (b - a).total_seconds() >= 0]
    return _median(gaps)


def match_list_sample(conn: sqlite3.Connection) -> dict[str, float | int | None]:
    rows = conn.execute(
        """
        SELECT c.competition_id, count(m.match_id) AS matches
        FROM competitions c
        JOIN http_checkpoint h
          ON h.path = '/v2/api/public/competitions/' || c.competition_id || '/matches'
         AND h.status = 'ok'
        LEFT JOIN matches m ON m.competition_id = c.competition_id
        GROUP BY c.competition_id
        """
    ).fetchall()
    if not rows:
        return {"competitions_with_match_lists": 0, "matches_in_sample": 0, "mean_matches": None}
    total = sum(int(row["matches"]) for row in rows)
    return {
        "competitions_with_match_lists": len(rows),
        "matches_in_sample": total,
        "mean_matches": total / len(rows),
    }


def build_report(conn: sqlite3.Connection, min_interval: float) -> dict[str, object]:
    stored = counts(conn)
    sample = match_list_sample(conn)
    seconds = measured_seconds_per_request(conn)
    competitions = int(stored["competitions"])
    categories = int(stored["tournament_categories"])
    seasons = int(stored["seasons"])
    federations = int(stored["federations"])
    mean = sample["mean_matches"]
    extrapolated_matches = None if mean is None or competitions == 0 else int(round(float(mean) * competitions))
    # One list call per federation-season, one category-bundle call per tournament
    # category, then per domestic competition: metadata, standings, player stats,
    # match list, and (when public) goalie stats. Each match then needs a detail
    # call for events and a lineup call. Each discovered player needs a profile
    # and a career call. National-team matches are extra and not in this figure.
    per_competition = 4
    per_match = 2
    listing_calls = seasons * federations * 2
    match_calls = 0 if extrapolated_matches is None else extrapolated_matches * per_match
    competition_calls = competitions * per_competition + categories
    known_players = int(
        conn.execute("SELECT count(DISTINCT player_id) FROM competition_player_stats").fetchone()[0]
    )
    requests_without_players = listing_calls + competition_calls + match_calls
    pace = seconds if seconds is not None else min_interval
    return {
        "stored": stored,
        "sample": sample,
        "measured_seconds_per_request": seconds,
        "pace_seconds_used": pace,
        "extrapolated_domestic_matches": extrapolated_matches,
        "known_players_in_fetched_stats": known_players,
        "listing_calls": listing_calls,
        "competition_calls": competition_calls,
        "match_calls": match_calls,
        "requests_without_player_profiles": requests_without_players,
        "hours_without_player_profiles": None
        if requests_without_players == 0
        else requests_without_players * pace / 3600,
    }


def format_report(report: dict[str, object]) -> str:
    stored = report["stored"]
    assert isinstance(stored, dict)
    sample = report["sample"]
    assert isinstance(sample, dict)
    lines = [
        f"seasons: {stored['seasons']}",
        f"federations: {stored['federations']}",
        f"competitions: {stored['competitions']}",
        f"tournament categories: {stored['tournament_categories']}",
        f"players: {stored['players']}",
        f"career rows: {stored['player_career_rows']}",
        f"matches: {stored['matches']}",
        f"match events: {stored['match_events']}",
        f"lineup rows: {stored['match_lineups']}",
        f"national competitions: {stored['national_competitions']}",
        (
            "match-list sample: "
            f"{sample['competitions_with_match_lists']} competitions, "
            f"{sample['matches_in_sample']} matches, "
            f"mean {sample['mean_matches']}"
        ),
        f"extrapolated domestic matches: {report['extrapolated_domestic_matches']}",
        f"measured seconds/request: {report['measured_seconds_per_request']}",
        (
            "full-run requests excluding player profiles and national match details: "
            f"{report['requests_without_player_profiles']}"
        ),
        f"hours at measured pace for that request count: {report['hours_without_player_profiles']}",
        (
            "distinct players seen in fetched competition stats (lower bound, not the population): "
            f"{report['known_players_in_fetched_stats']}"
        ),
    ]
    return "\n".join(lines)

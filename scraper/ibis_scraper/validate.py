"""Compare stored career rows with the hand-collected player fixtures."""

from __future__ import annotations

import json
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

METRICS = ("matches", "goals", "assists", "points", "penalty_minutes")


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(unicodedata.normalize("NFC", str(value)).split())


def _metric(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def load_fixture(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for season, entries in data["seasons"].items():
        for entry in entries:
            competition, club, matches, goals, assists, points, pim = entry
            if _norm(competition).casefold() == "totalt":
                continue
            rows.append(
                {
                    "season": _norm(season),
                    "competition": _norm(competition),
                    "club": _norm(club),
                    "matches": _metric(matches),
                    "goals": _metric(goals),
                    "assists": _metric(assists),
                    "points": _metric(points),
                    "penalty_minutes": _metric(pim),
                }
            )
    data["rows"] = rows
    return data


def career_rows_from_db(conn, player_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT season_name,
               coalesce(competition_name, competition_category_name) AS competition_name,
               team_name, matches, goals, assists, points, penalty_minutes
        FROM player_career_rows
        WHERE player_id = ?
        """,
        (player_id,),
    ).fetchall()
    return [
        {
            "season": _norm(row["season_name"]),
            "competition": _norm(row["competition_name"]),
            "club": _norm(row["team_name"]),
            "matches": row["matches"],
            "goals": row["goals"],
            "assists": row["assists"],
            "points": row["points"],
            "penalty_minutes": row["penalty_minutes"],
        }
        for row in rows
    ]


def _key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row["season"],
        row["competition"],
        row["club"],
        row["matches"],
        row["goals"],
        row["assists"],
        row["points"],
        row["penalty_minutes"],
    )


def compare_player(conn, fixture: dict[str, Any]) -> list[str]:
    player_id = int(fixture["player_id"])
    problems: list[str] = []
    stored = conn.execute(
        "SELECT name, birth_year FROM players WHERE player_id = ?",
        (player_id,),
    ).fetchone()
    if stored is None:
        return [f"{player_id}: player was not stored"]
    if _norm(stored["name"]) != _norm(fixture["name"]):
        problems.append(f"{player_id}: name {stored['name']!r} != {fixture['name']!r}")
    if stored["birth_year"] != fixture["birth_year"]:
        problems.append(
            f"{player_id}: birth year {stored['birth_year']!r} != {fixture['birth_year']!r}"
        )
    expected = Counter(_key(row) for row in fixture["rows"])
    actual = Counter(_key(row) for row in career_rows_from_db(conn, player_id))
    missing = expected - actual
    extra = actual - expected
    if missing:
        sample = next(iter(missing.elements()))
        problems.append(f"{player_id}: missing {sum(missing.values())} fixture rows, e.g. {sample}")
    if extra:
        sample = next(iter(extra.elements()))
        problems.append(f"{player_id}: {sum(extra.values())} unexpected rows, e.g. {sample}")
    return problems


def summarize_fixture(fixture: dict[str, Any]) -> dict[str, int | None]:
    rows = fixture["rows"]
    seasons = {row["season"] for row in rows}

    def total(metric: str) -> int | None:
        values = [row[metric] for row in rows]
        if any(value is None for value in values):
            return None
        return sum(values)  # type: ignore[arg-type]

    return {
        "rows": len(rows),
        "seasons": len(seasons),
        "matches": total("matches"),
        "goals": total("goals"),
        "assists": total("assists"),
        "points": total("points"),
        "penalty_minutes": total("penalty_minutes"),
    }


def validate_directory(conn, directory: Path) -> tuple[list[str], dict[str, dict[str, int | None]]]:
    problems: list[str] = []
    summaries: dict[str, dict[str, int | None]] = {}
    for path in sorted(directory.glob("*.json")):
        fixture = load_fixture(path)
        summaries[str(fixture["player_id"])] = summarize_fixture(fixture)
        problems.extend(compare_player(conn, fixture))
    return problems, summaries

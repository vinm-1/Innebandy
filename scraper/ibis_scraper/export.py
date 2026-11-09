"""Export staging tables to JSON and CSV. Nulls stay null in JSON."""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

TABLES = [
    "seasons",
    "federations",
    "competitions",
    "competition_federations",
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
    "match_period_shots",
    "match_events",
    "match_lineups",
    "match_staff",
    "national_teams",
    "national_competitions",
    "national_matches",
    "national_match_events",
]


def export_all(conn: sqlite3.Connection, directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for table in TABLES:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        records = [dict(row) for row in rows]
        json_path = directory / f"{table}.json"
        json_path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        csv_path = directory / f"{table}.csv"
        fieldnames = list(records[0].keys()) if records else [d[1] for d in conn.execute(f"PRAGMA table_info({table})")]
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for record in records:
                writer.writerow({key: "" if value is None else value for key, value in record.items()})
        written.extend([json_path, csv_path])
    return written

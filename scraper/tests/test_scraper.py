import json
import tempfile
import unittest
from pathlib import Path

from ibis_scraper.classify import label_kind
from ibis_scraper.client import canonical_string, sign_request
from ibis_scraper.db import (
    connect,
    player_id_or_none,
    replace_career_rows,
    stated_penalty_minutes,
    upsert_match,
    upsert_player,
)
from ibis_scraper.robots import matching_group, parse_robots, path_allowed
from ibis_scraper.validate import load_fixture, summarize_fixture, validate_directory

ROBOTS = """
User-agent: *
Allow: /

User-agent: GPTBot
User-agent: ClaudeBot
Disallow: /
"""

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
UA = "IbisHistoricalStatsScraper/0.1 (test)"


class RobotsTests(unittest.TestCase):
    def test_star_is_allowed_and_named_bots_are_not(self):
        groups = parse_robots(ROBOTS)
        ours = matching_group(groups, UA)
        self.assertTrue(path_allowed(ours, "/spelare/114860"))
        blocked = matching_group(groups, "Mozilla/5.0 GPTBot/1.0")
        self.assertFalse(path_allowed(blocked, "/"))


class SignTests(unittest.TestCase):
    def test_canonical_shape_and_hmac(self):
        body = b""
        text = canonical_string("get", "/v2/api/public/players/114860", 1000, "nonce", body)
        self.assertEqual(
            text,
            "GET\n/v2/api/public/players/114860\n1000\nnonce\n"
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        signature = sign_request("secret", "GET", "/v2/api/public/players/114860", 1000, "nonce", body)
        self.assertEqual(len(signature), 64)


class ClassifyTests(unittest.TestCase):
    def test_label_rules_from_the_hand_collected_careers(self):
        self.assertEqual(label_kind("Herrar Division 4 Södra"), "regular")
        self.assertEqual(label_kind("Allsvenskan Damer Norra"), "regular")
        self.assertEqual(label_kind("SSL - Svenska Superligan Damer"), "regular")
        self.assertEqual(label_kind("SSL - Slutspel Damer"), "playoff")
        self.assertEqual(label_kind("Kval till H1"), "qualification")
        self.assertEqual(label_kind("DM herrar"), "cup")
        self.assertEqual(label_kind("City Cup herrar"), "cup")
        self.assertEqual(label_kind("Innebandyfesten"), "cup")
        self.assertEqual(label_kind("Bäst i Stan Herr"), "cup")
        self.assertEqual(label_kind("JAS HJ18 Play Off"), "youth")
        self.assertEqual(label_kind("Juniorallsvenskan Norra Norrland"), "youth")
        self.assertEqual(label_kind("SDF-SM HJ17"), "youth")
        self.assertEqual(label_kind("Herrar utveckling Elit"), "other")
        self.assertEqual(label_kind("Dam-VM"), "national_team")
        self.assertIsNone(label_kind("Förbundsdomarkurs"))
        self.assertEqual(label_kind("Women's World Championships", national_team=True), "national_team")


class PenaltyTests(unittest.TestCase):
    def test_minutes_are_read_only_from_the_penalty_name(self):
        self.assertEqual(stated_penalty_minutes("Otillåten trängning, 2 min"), 2)
        self.assertEqual(stated_penalty_minutes("2+2 min"), 4)
        self.assertIsNone(stated_penalty_minutes("Lindrigt matchstraff, Sabotage av spelet"))
        self.assertIsNone(stated_penalty_minutes(""))


class HeadToHeadTests(unittest.TestCase):
    def test_opponents_and_same_team_with_event_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "t.sqlite")
            conn.execute("INSERT INTO seasons (season_id, name, is_current, captured_at) VALUES (29, '2011/12', 0, 't')")
            for match_id, home, away in ((1, "Sundsvall City IBC", "Ersboda SK"), (2, "Sundsvall City IBC", "Ersboda SK")):
                upsert_match(
                    conn,
                    {
                        "MatchID": match_id,
                        "SeasonID": 29,
                        "CompetitionID": 25003,
                        "CompetitionName": "Herrar Division 1 Norra Norrland",
                        "HomeTeam": home,
                        "AwayTeam": away,
                        "HomeTeamID": 10,
                        "AwayTeamID": 20,
                        "GoalsHomeTeam": 5,
                        "GoalsAwayTeam": 3,
                        "Events": [
                            {
                                "MatchEventID": match_id * 10 + 1,
                                "MatchEventType": "Mål",
                                "PlayerID": 114860,
                                "PlayerName": "Victor Molén Nyqvist",
                                "PlayerAssistID": 0,
                                "Minute": 3,
                                "Second": 10,
                                "GoalsHomeTeam": 1,
                                "GoalsAwayTeam": 0,
                            },
                            {
                                "MatchEventID": match_id * 10 + 2,
                                "MatchEventType": "Utvisning",
                                "PlayerID": 378994,
                                "PlayerName": "Robin Jonsson",
                                "PenaltyName": "Otillåten trängning, 2 min",
                                "Minute": 8,
                                "Second": 0,
                            },
                        ],
                    },
                    detailed=True,
                )
            # Match 1: opposing teams. Match 2: same team.
            conn.execute(
                """
                INSERT INTO match_lineups (match_id, side, player_id, name, shirt_no, captured_at)
                VALUES
                    (1, 'home', 114860, 'Victor Molén Nyqvist', 19, 't'),
                    (1, 'away', 378994, 'Robin Jonsson', 9, 't'),
                    (2, 'home', 114860, 'Victor Molén Nyqvist', 19, 't'),
                    (2, 'home', 378994, 'Robin Jonsson', 9, 't')
                """
            )
            conn.commit()
            opponents = conn.execute(
                """
                SELECT player_a_goals, player_a_assists, player_a_stated_penalty_minutes,
                       player_b_goals, player_b_stated_penalty_minutes, relation,
                       player_a_team_name, player_b_team_name
                FROM player_head_to_head
                WHERE player_a_id = 114860 AND player_b_id = 378994 AND relation = 'opponents'
                """
            ).fetchone()
            same = conn.execute(
                """
                SELECT relation, player_a_shirt, player_b_shirt
                FROM player_head_to_head
                WHERE player_a_id = 114860 AND player_b_id = 378994 AND relation = 'same_team'
                """
            ).fetchone()
            self.assertEqual(opponents["relation"], "opponents")
            self.assertEqual(opponents["player_a_goals"], 1)
            self.assertEqual(opponents["player_a_assists"], 0)
            self.assertEqual(opponents["player_a_stated_penalty_minutes"], 0)
            self.assertEqual(opponents["player_b_goals"], 0)
            self.assertEqual(opponents["player_b_stated_penalty_minutes"], 2)
            self.assertEqual(opponents["player_a_team_name"], "Sundsvall City IBC")
            self.assertEqual(opponents["player_b_team_name"], "Ersboda SK")
            self.assertEqual(same["relation"], "same_team")
            self.assertEqual(same["player_a_shirt"], 19)
            self.assertEqual(same["player_b_shirt"], 9)
            conn.close()


class StoreTests(unittest.TestCase):
    def test_assist_id_zero_is_null_and_match_detail_updates_list_row(self):
        self.assertIsNone(player_id_or_none(0))
        self.assertIsNone(player_id_or_none(None))
        self.assertEqual(player_id_or_none(114860), 114860)
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "t.sqlite")
            upsert_match(
                conn,
                {
                    "MatchID": 1767161,
                    "CompetitionID": 45603,
                    "CompetitionName": "Champions Cup",
                    "HomeTeamID": 1,
                    "HomeTeam": "Home",
                    "AwayTeamID": 2,
                    "AwayTeam": "Away",
                    "GoalsHomeTeam": 1,
                    "GoalsAwayTeam": 0,
                },
                detailed=False,
            )
            upsert_match(
                conn,
                {
                    "MatchID": 1767161,
                    "GoalsHomeTeam": 12,
                    "GoalsAwayTeam": 0,
                    "Spectators": 166,
                    "Events": [
                        {
                            "MatchEventID": 9,
                            "MatchEventTypeID": 1,
                            "MatchEventType": "Mål",
                            "PlayerID": 177955,
                            "PlayerName": "Ellen Bäckstedt",
                            "PlayerAssistID": 0,
                            "PlayerAssistName": "",
                            "GoalsHomeTeam": 1,
                            "GoalsAwayTeam": 0,
                        }
                    ],
                },
                detailed=True,
            )
            conn.commit()
            match = conn.execute("SELECT goals_home, spectators FROM matches").fetchone()
            event = conn.execute(
                "SELECT player_id, assist_player_id, assist_player_name FROM match_events"
            ).fetchone()
            self.assertEqual(match["goals_home"], 12)
            self.assertEqual(match["spectators"], 166)
            self.assertEqual(event["player_id"], 177955)
            self.assertIsNone(event["assist_player_id"])
            self.assertIsNone(event["assist_player_name"])
            conn.close()


class FixtureTests(unittest.TestCase):
    def test_reference_totals_and_round_trip(self):
        expected = {
            "114860": {"matches": 240, "points": 255, "seasons": 13, "rows": 28},
            "426689": {"rows": 28, "seasons": 19},
            "241859": {"rows": 74, "seasons": 21},
            "508042": {"rows": 37, "seasons": 12},
        }
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "t.sqlite")
            for path in sorted(FIXTURES.glob("*.json")):
                fixture = load_fixture(path)
                summary = summarize_fixture(fixture)
                player_id = str(fixture["player_id"])
                for key, value in expected[player_id].items():
                    self.assertEqual(summary[key], value, f"{player_id} {key}")
                upsert_player(
                    conn,
                    {
                        "PlayerID": int(player_id),
                        "Name": fixture["name"],
                        "BirthYear": fixture["birth_year"],
                    },
                )
                rows = []
                raw = json.loads(path.read_text(encoding="utf-8"))
                for season, entries in raw["seasons"].items():
                    for entry in entries:
                        if str(entry[0]).casefold() == "totalt":
                            continue
                        rows.append(
                            {
                                "Season": season,
                                "Competition": entry[0],
                                "Team": entry[1],
                                "Matches": entry[2],
                                "Goals": entry[3],
                                "Assists": entry[4],
                                "Points": entry[5],
                                "PenaltyMinutes": entry[6],
                            }
                        )
                replace_career_rows(conn, int(player_id), rows)
            conn.commit()
            problems, _ = validate_directory(conn, FIXTURES)
            self.assertEqual(problems, [])
            conn.close()


if __name__ == "__main__":
    unittest.main()

"""Command line for the iBIS historical scraper.

Examples
--------
Four reference players::

    python -m ibis_scraper scrape --player-id 114860 --player-id 426689 \\
        --player-id 241859 --player-id 508042

One competition, including matches, events and lineups::

    python -m ibis_scraper scrape --competition-id 45603 --matches

Competition lists for every federation and season, without match pages::

    python -m ibis_scraper scrape --full --lists-only

Everything the public API exposes for those entities::

    python -m ibis_scraper scrape --full --matches --with-profiles
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ibis_scraper.client import DEFAULT_USER_AGENT, IbisClient
from ibis_scraper.crawl import Crawler, Scope
from ibis_scraper.db import connect, set_meta
from ibis_scraper.export import export_all
from ibis_scraper.report import build_report, format_report
from ibis_scraper.robots import crawl_delay_seconds, matching_group, parse_robots, path_allowed
from ibis_scraper.validate import validate_directory

LOG = logging.getLogger("ibis")


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", type=Path, default=Path("data/ibis.sqlite"))
    common.add_argument("--min-interval", type=float, default=1.0, help="Minimum seconds between request starts.")
    common.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    common.add_argument("--refresh", action="store_true", help="Refetch resources that already succeeded.")
    common.add_argument("-v", "--verbose", action="store_true")
    parser = argparse.ArgumentParser(
        prog="ibis_scraper",
        description="Collect public iBIS floorball statistics.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scrape = sub.add_parser("scrape", parents=[common], help="Fetch a scoped or full slice into the staging database.")
    scrape.add_argument("--full", action="store_true", help="All seasons and federations. Required for an unscoped run.")
    scrape.add_argument("--lists-only", action="store_true", help="Store seasons, federations and competition lists only.")
    scrape.add_argument("--season", action="append", default=[], help="Season name such as 2025/26, or a numeric season id.")
    scrape.add_argument("--federation-id", action="append", type=int, default=[])
    scrape.add_argument("--competition-id", action="append", type=int, default=[])
    scrape.add_argument("--player-id", action="append", type=int, default=[])
    scrape.add_argument("--match-id", action="append", type=int, default=[])
    scrape.add_argument("--association-id", action="append", type=int, default=[])
    scrape.add_argument("--matches", action="store_true", help="Also collect match lists, events and lineups.")
    scrape.add_argument("--no-events", action="store_true")
    scrape.add_argument("--no-lineups", action="store_true")
    scrape.add_argument("--tournaments", action="store_true", help="Include cup/tournament categories.")
    scrape.add_argument("--national-teams", action="store_true")
    scrape.add_argument("--with-profiles", action="store_true", help="Fetch player pages seen on competition stat tables.")
    scrape.add_argument("--sample-match-lists", type=int, default=0, help="Fetch this many match lists spread across stored competitions.")

    export = sub.add_parser("export", parents=[common], help="Write JSON and CSV for every staging table.")
    export.add_argument("--out", type=Path, default=Path("data/export"))

    validate = sub.add_parser("validate", parents=[common], help="Compare stored careers with hand-collected fixtures.")
    validate.add_argument("--fixtures", type=Path, default=Path("fixtures"))

    sub.add_parser("estimate", parents=[common], help="Print stored counts and a full-run time estimate.")
    sub.add_parser(
        "coverage",
        parents=[common],
        help="Sample one played match per stored season and record whether lineups and events came back.",
    )
    return parser


def _scope_from_args(args: argparse.Namespace) -> Scope:
    season_ids: set[int] = set()
    season_names: set[str] = set()
    for value in args.season:
        if value.isdigit():
            season_ids.add(int(value))
        else:
            season_names.add(value)
    scope = Scope(
        full=args.full,
        lists_only=args.lists_only,
        season_ids=season_ids,
        season_names=season_names,
        federation_ids=set(args.federation_id),
        competition_ids=set(args.competition_id),
        player_ids=set(args.player_id),
        match_ids=set(args.match_id),
        association_ids=set(args.association_id),
        matches=args.matches or args.full,
        events=not args.no_events,
        lineups=not args.no_lineups,
        tournaments=args.tournaments or args.full,
        national_teams=args.national_teams or args.full,
        player_profiles=args.with_profiles or args.full,
        sample_match_lists=args.sample_match_lists,
        refresh=args.refresh,
    )
    has_target = any(
        [
            scope.full,
            scope.season_ids,
            scope.season_names,
            scope.federation_ids,
            scope.competition_ids,
            scope.player_ids,
            scope.match_ids,
            scope.association_ids,
            scope.sample_match_lists,
        ]
    )
    if not has_target:
        raise SystemExit("refusing an empty scrape. Pass --full or a season, competition, player, match or association.")
    if scope.association_ids and not (scope.full or scope.season_ids or scope.season_names or scope.competition_ids):
        raise SystemExit("--association-id needs --season, --competition-id or --full so the crawl stays bounded.")
    if scope.lists_only:
        scope.matches = False
        scope.player_profiles = False
    return scope


def _check_robots(client: IbisClient) -> None:
    status, text = client.fetch_text("https://stats.innebandy.se/robots.txt")
    if status != 200:
        raise SystemExit(f"refusing to crawl; stats.innebandy.se/robots.txt returned HTTP {status}")
    group = matching_group(parse_robots(text), client.user_agent)
    if not path_allowed(group, "/") or not path_allowed(group, "/spelare/"):
        raise SystemExit("robots.txt disallows this user agent. Not scraping.")
    delay = crawl_delay_seconds(group)
    if delay is not None and delay > client.min_interval:
        LOG.info("raising interval from %s to robots crawl-delay %s", client.min_interval, delay)
        client.min_interval = delay
    LOG.info("robots.txt allows this user agent on the public stats site")


def _print_coverage(conn) -> None:
    rows = conn.execute(
        """
        SELECT season_name, competition_name, match_id, events, goal_events,
               penalty_events, lineup_players, lineup_player_ids, note
        FROM coverage_samples
        ORDER BY season_name
        """
    ).fetchall()
    print(
        "season\tcompetition\tmatch\tevents\tgoals\tpenalties\tlineup_rows\tlineup_ids\tnote"
    )
    for row in rows:
        print(
            "\t".join(
                "" if value is None else str(value)
                for value in (
                    row["season_name"],
                    row["competition_name"],
                    row["match_id"],
                    row["events"],
                    row["goal_events"],
                    row["penalty_events"],
                    row["lineup_players"],
                    row["lineup_player_ids"],
                    row["note"],
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose or args.command == "scrape" else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if args.min_interval < 1.0 and args.command == "scrape":
        LOG.warning("min-interval %s is faster than the default 1 request/second", args.min_interval)
    conn = connect(args.db)
    try:
        if args.command == "export":
            paths = export_all(conn, args.out)
            print(f"wrote {len(paths)} files under {args.out}")
            return 0
        if args.command == "validate":
            problems, summaries = validate_directory(conn, args.fixtures)
            for player_id, summary in summaries.items():
                print(f"{player_id}: {summary}")
            if problems:
                print("FAILED")
                for problem in problems:
                    print(problem)
                return 1
            print("PASSED")
            return 0
        if args.command == "estimate":
            print(format_report(build_report(conn, args.min_interval)))
            return 0
        if args.command == "coverage":
            client = IbisClient(min_interval=args.min_interval, user_agent=args.user_agent)
            try:
                _check_robots(client)
                crawler = Crawler(client, conn, Scope(refresh=args.refresh))
                crawler.sample_season_coverage()
                _print_coverage(conn)
            finally:
                client.close()
            return 0
        scope = _scope_from_args(args)
        client = IbisClient(min_interval=args.min_interval, user_agent=args.user_agent)
        try:
            _check_robots(client)
            set_meta(conn, "user_agent", client.user_agent)
            set_meta(conn, "min_interval", str(client.min_interval))
            conn.commit()
            crawler = Crawler(client, conn, scope)
            crawler.run()
            print(f"requests this run: {crawler.requests}")
            print(format_report(build_report(conn, client.min_interval)))
        finally:
            client.close()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

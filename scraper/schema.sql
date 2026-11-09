-- Staging schema for public iBIS statistics.
-- Primary keys are iBIS identifiers when the source provides them.
-- Columns that the source does not populate stay NULL.
-- label_kind is a documented label classification, not a source field.
-- captured_at is when this scraper stored the row.

PRAGMA foreign_keys = OFF;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS http_checkpoint (
    path TEXT PRIMARY KEY,
    status TEXT NOT NULL,          -- ok, absent, forbidden, error
    http_status INTEGER,
    fetched_at TEXT NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS http_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    http_status INTEGER,
    elapsed_ms INTEGER,
    bytes INTEGER,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seasons (
    season_id INTEGER PRIMARY KEY,
    name TEXT,
    is_current INTEGER,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS federations (
    federation_id INTEGER PRIMARY KEY,
    name TEXT,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS competitions (
    competition_id INTEGER PRIMARY KEY,
    season_id INTEGER,
    season_name TEXT,
    name TEXT,
    category_id INTEGER,
    category_name TEXT,
    competition_type_id INTEGER,
    competition_status_id INTEGER,
    playoff_category_id INTEGER,
    playoff_type_name TEXT,
    max_matches_in_series INTEGER,
    num_promoted_teams INTEGER,
    num_demoted_teams INTEGER,
    num_playoff_teams_up INTEGER,
    num_playoff_teams_down INTEGER,
    player_statistics_public INTEGER,
    goalie_statistics_public INTEGER,
    team_statistics_public INTEGER,
    logotype_url TEXT,
    label_kind TEXT,
    source_path TEXT,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS competition_federations (
    competition_id INTEGER NOT NULL,
    federation_id INTEGER NOT NULL,
    PRIMARY KEY (competition_id, federation_id)
);

CREATE TABLE IF NOT EXISTS tournament_categories (
    season_id INTEGER NOT NULL,
    federation_id INTEGER NOT NULL,
    competition_category_id INTEGER NOT NULL,
    name TEXT,
    captured_at TEXT NOT NULL,
    PRIMARY KEY (season_id, federation_id, competition_category_id)
);

CREATE TABLE IF NOT EXISTS associations (
    association_id INTEGER PRIMARY KEY,
    name TEXT,
    logotype_url TEXT,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS teams (
    team_id INTEGER PRIMARY KEY,
    name TEXT,
    short_name TEXT,
    association_id INTEGER,
    logotype_url TEXT,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS players (
    player_id INTEGER PRIMARY KEY,
    person_id INTEGER,
    name TEXT,
    birth_year INTEGER,
    age_reported INTEGER,
    shirt_no INTEGER,
    position_id INTEGER,
    position TEXT,
    licensed_association_id INTEGER,
    licensed_association_name TEXT,
    origin_association_id INTEGER,
    origin_association_name TEXT,
    -- Totals as printed on the player object. These are NOT recomputed
    -- from career rows; the two sources can disagree.
    summary_matches INTEGER,
    summary_goals INTEGER,
    summary_assists INTEGER,
    summary_points INTEGER,
    summary_penalty_minutes INTEGER,
    source_url TEXT,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS license_history (
    license_id INTEGER PRIMARY KEY,
    player_id INTEGER NOT NULL,
    season_id INTEGER,
    season_name TEXT,
    association_id INTEGER,
    association_name TEXT,
    start_date TEXT,
    end_date TEXT,
    license_type_id INTEGER,
    license_type_name TEXT,
    captured_at TEXT NOT NULL
);

-- Season rows from GET /players/{id}/career. TOTALT rows are not in this payload.
CREATE TABLE IF NOT EXISTS player_career_rows (
    player_id INTEGER NOT NULL,
    season_id INTEGER,
    season_name TEXT NOT NULL,
    competition_id INTEGER,
    competition_name TEXT,
    competition_category_id INTEGER,
    competition_category_name TEXT,
    team_id INTEGER,
    team_name TEXT,
    matches INTEGER,
    goals INTEGER,
    assists INTEGER,
    points INTEGER,
    penalty_minutes INTEGER,
    current_season INTEGER,
    captured_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_player_career_rows
    ON player_career_rows (
        player_id,
        ifnull(season_id, -1),
        season_name,
        ifnull(competition_id, -1),
        ifnull(competition_name, ''),
        ifnull(competition_category_id, -1),
        ifnull(competition_category_name, ''),
        ifnull(team_id, -1),
        ifnull(team_name, '')
    );

-- Rows from GET /competitions/{id}/playerstatistics.
CREATE TABLE IF NOT EXISTS competition_player_stats (
    competition_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    team_id INTEGER,
    player_name TEXT,
    team_name TEXT,
    association_id INTEGER,
    association_name TEXT,
    matches INTEGER,
    goals INTEGER,
    assists INTEGER,
    points INTEGER,
    penalty_minutes INTEGER,
    goals_pp INTEGER,
    goals_bp INTEGER,
    assists_pp INTEGER,
    assists_bp INTEGER,
    points_pp INTEGER,
    points_bp INTEGER,
    captured_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_competition_player_stats
    ON competition_player_stats (
        competition_id,
        player_id,
        ifnull(team_id, -1)
    );

CREATE TABLE IF NOT EXISTS goalie_stats (
    competition_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    team_id INTEGER,
    player_name TEXT,
    team_name TEXT,
    team_short_name TEXT,
    association_id INTEGER,
    association_name TEXT,
    matches INTEGER,
    shots_against INTEGER,
    goals_against INTEGER,
    save_percentage REAL,
    captured_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_goalie_stats
    ON goalie_stats (
        competition_id,
        player_id,
        ifnull(team_id, -1)
    );

CREATE TABLE IF NOT EXISTS standings (
    competition_id INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    team_name TEXT,
    team_status_id INTEGER,
    position INTEGER,
    played_home INTEGER,
    wins_home INTEGER,
    draws_home INTEGER,
    losses_home INTEGER,
    sudden_death_wins_home INTEGER,
    goals_for_home INTEGER,
    goals_against_home INTEGER,
    played_away INTEGER,
    wins_away INTEGER,
    draws_away INTEGER,
    losses_away INTEGER,
    sudden_death_wins_away INTEGER,
    goals_for_away INTEGER,
    goals_against_away INTEGER,
    scoring_diff INTEGER,
    points INTEGER,
    last_games_json TEXT,
    captured_at TEXT NOT NULL,
    PRIMARY KEY (competition_id, team_id)
);

CREATE TABLE IF NOT EXISTS matches (
    match_id INTEGER PRIMARY KEY,
    competition_id INTEGER,
    competition_name TEXT,
    season_id INTEGER,
    match_datetime TEXT,
    match_no TEXT,
    match_status INTEGER,
    home_team_id INTEGER,
    home_team_name TEXT,
    home_team_short_name TEXT,
    away_team_id INTEGER,
    away_team_name TEXT,
    away_team_short_name TEXT,
    goals_home INTEGER,
    goals_away INTEGER,
    has_final_result INTEGER,
    match_result_type_id INTEGER,
    cancelled INTEGER,
    postponed INTEGER,
    abandoned INTEGER,
    note TEXT,
    round INTEGER,
    round_name TEXT,
    venue_id INTEGER,
    venue TEXT,
    main_venue_id INTEGER,
    main_venue TEXT,
    arranging_association_id INTEGER,
    arranging_association TEXT,
    spectators INTEGER,
    home_match_team_id INTEGER,
    away_match_team_id INTEGER,
    referee1_id INTEGER,
    referee1_name TEXT,
    referee2_id INTEGER,
    referee2_name TEXT,
    home_shirt_color TEXT,
    away_shirt_color TEXT,
    detail_captured_at TEXT,
    captured_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_matches_competition ON matches (competition_id);
CREATE INDEX IF NOT EXISTS ix_matches_teams ON matches (home_team_id, away_team_id);

CREATE TABLE IF NOT EXISTS match_period_shots (
    match_id INTEGER NOT NULL,
    period INTEGER NOT NULL,
    shots_home INTEGER,
    shots_away INTEGER,
    captured_at TEXT NOT NULL,
    PRIMARY KEY (match_id, period)
);

CREATE TABLE IF NOT EXISTS match_events (
    match_event_id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL,
    event_type_id INTEGER,
    event_type TEXT,
    match_team_id INTEGER,
    period INTEGER,
    period_name TEXT,
    minute INTEGER,
    second INTEGER,
    player_id INTEGER,
    player_shirt_no INTEGER,
    player_name TEXT,
    assist_player_id INTEGER,
    assist_shirt_no INTEGER,
    assist_player_name TEXT,
    penalty_code TEXT,
    penalty_name TEXT,
    -- Minutes only when PenaltyName states them ("2 min", "2+2 min").
    -- A match penalty with no stated length stays NULL.
    penalty_minutes INTEGER,
    goals_home INTEGER,
    goals_away INTEGER,
    captured_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_match_events_match ON match_events (match_id);
CREATE INDEX IF NOT EXISTS ix_match_events_player ON match_events (player_id);
CREATE INDEX IF NOT EXISTS ix_match_events_assist ON match_events (assist_player_id);

CREATE TABLE IF NOT EXISTS match_lineups (
    match_id INTEGER NOT NULL,
    side TEXT NOT NULL,             -- home, away
    player_id INTEGER,
    match_player_id INTEGER,
    name TEXT,
    shirt_no INTEGER,
    captain INTEGER,
    line INTEGER,
    position_id INTEGER,
    position TEXT,
    shots_on_goal INTEGER,
    goals_against INTEGER,
    save_percentage REAL,
    captured_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_match_lineups
    ON match_lineups (
        match_id,
        side,
        ifnull(match_player_id, -1),
        ifnull(player_id, -1)
    );

CREATE INDEX IF NOT EXISTS ix_match_lineups_player ON match_lineups (player_id);

-- Public role names only. Phone numbers and email addresses are not stored.
CREATE TABLE IF NOT EXISTS match_staff (
    match_id INTEGER NOT NULL,
    side TEXT NOT NULL,
    person_id INTEGER,
    name TEXT,
    role_id INTEGER,
    role_name TEXT,
    captured_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_match_staff
    ON match_staff (
        match_id,
        side,
        ifnull(person_id, -1),
        ifnull(role_id, -1),
        ifnull(name, '')
    );

CREATE TABLE IF NOT EXISTS national_teams (
    iff_team_id INTEGER PRIMARY KEY,
    name TEXT,
    short_name TEXT,
    logotype_url TEXT,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS national_competitions (
    iff_competition_id INTEGER PRIMARY KEY,
    iff_team_id INTEGER,
    name TEXT,
    year INTEGER,
    start_date TEXT,
    label_kind TEXT,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS national_matches (
    match_id INTEGER PRIMARY KEY,
    iff_competition_id INTEGER,
    competition_name TEXT,
    match_datetime TEXT,
    match_no TEXT,
    match_status INTEGER,
    home_team_id INTEGER,
    home_team_name TEXT,
    away_team_id INTEGER,
    away_team_name TEXT,
    goals_home INTEGER,
    goals_away INTEGER,
    has_final_result INTEGER,
    venue TEXT,
    spectators INTEGER,
    detail_captured_at TEXT,
    captured_at TEXT NOT NULL
);

-- One played match sampled per season to see whether that season returned
-- lineups and events, or only a result. This is a coverage probe, not a full scrape.
CREATE TABLE IF NOT EXISTS coverage_samples (
    season_id INTEGER NOT NULL,
    season_name TEXT,
    competition_id INTEGER,
    competition_name TEXT,
    match_id INTEGER,
    match_datetime TEXT,
    events INTEGER,
    goal_events INTEGER,
    penalty_events INTEGER,
    lineup_players INTEGER,
    lineup_player_ids INTEGER,
    note TEXT,
    captured_at TEXT NOT NULL,
    PRIMARY KEY (season_id, match_id)
);

CREATE TABLE IF NOT EXISTS national_match_events (
    match_event_id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL,
    event_type_id INTEGER,
    event_type TEXT,
    period INTEGER,
    minute INTEGER,
    second INTEGER,
    player_id INTEGER,
    player_name TEXT,
    assist_player_id INTEGER,
    assist_player_name TEXT,
    penalty_code TEXT,
    penalty_name TEXT,
    goals_home INTEGER,
    goals_away INTEGER,
    captured_at TEXT NOT NULL
);

-- VIEWS
-- Head-to-head rows come from lineups, so a player who is not in a lineup
-- cannot be placed on a team. Event totals are 0 only after that match's
-- detail page was stored (matches.detail_captured_at). Otherwise they stay
-- NULL. penalty minutes are the sum of lengths the penalty name states.

DROP VIEW IF EXISTS player_head_to_head;
DROP VIEW IF EXISTS player_match_event_totals;
DROP VIEW IF EXISTS lineup_players;

CREATE VIEW lineup_players AS
SELECT
    match_id,
    side,
    player_id,
    min(name) AS name,
    min(shirt_no) AS shirt_no
FROM match_lineups
WHERE player_id IS NOT NULL
GROUP BY match_id, side, player_id;

CREATE VIEW player_match_event_totals AS
SELECT
    match_id,
    player_id,
    sum(is_goal) AS goals,
    sum(is_assist) AS assists,
    sum(penalty_minutes) AS stated_penalty_minutes,
    sum(CASE WHEN is_penalty = 1 AND penalty_minutes IS NULL THEN 1 ELSE 0 END)
        AS penalties_without_stated_minutes
FROM (
    SELECT match_id, player_id, 1 AS is_goal, 0 AS is_assist, 0 AS is_penalty, NULL AS penalty_minutes
    FROM match_events
    WHERE event_type IN ('Mål', 'Straffmål') AND player_id IS NOT NULL
    UNION ALL
    SELECT match_id, assist_player_id, 0, 1, 0, NULL
    FROM match_events
    WHERE event_type IN ('Mål', 'Straffmål') AND assist_player_id IS NOT NULL
    UNION ALL
    SELECT match_id, player_id, 0, 0, 1, penalty_minutes
    FROM match_events
    WHERE event_type = 'Utvisning' AND player_id IS NOT NULL
)
GROUP BY match_id, player_id;

CREATE VIEW player_head_to_head AS
SELECT
    m.match_id,
    m.match_datetime,
    m.season_id,
    s.name AS season_name,
    m.competition_id,
    m.competition_name,
    m.goals_home,
    m.goals_away,
    CASE WHEN a.side = b.side THEN 'same_team' ELSE 'opponents' END AS relation,
    a.player_id AS player_a_id,
    a.name AS player_a_name,
    a.shirt_no AS player_a_shirt,
    a.side AS player_a_side,
    CASE WHEN a.side = 'home' THEN m.home_team_id ELSE m.away_team_id END AS player_a_team_id,
    CASE WHEN a.side = 'home' THEN m.home_team_name ELSE m.away_team_name END AS player_a_team_name,
    b.player_id AS player_b_id,
    b.name AS player_b_name,
    b.shirt_no AS player_b_shirt,
    b.side AS player_b_side,
    CASE WHEN b.side = 'home' THEN m.home_team_id ELSE m.away_team_id END AS player_b_team_id,
    CASE WHEN b.side = 'home' THEN m.home_team_name ELSE m.away_team_name END AS player_b_team_name,
    CASE WHEN m.detail_captured_at IS NOT NULL THEN coalesce(ea.goals, 0) ELSE ea.goals END AS player_a_goals,
    CASE WHEN m.detail_captured_at IS NOT NULL THEN coalesce(ea.assists, 0) ELSE ea.assists END AS player_a_assists,
    CASE
        WHEN m.detail_captured_at IS NOT NULL AND coalesce(ea.penalties_without_stated_minutes, 0) = 0
        THEN coalesce(ea.stated_penalty_minutes, 0)
        ELSE ea.stated_penalty_minutes
    END AS player_a_stated_penalty_minutes,
    coalesce(ea.penalties_without_stated_minutes, 0) AS player_a_penalties_without_stated_minutes,
    CASE WHEN m.detail_captured_at IS NOT NULL THEN coalesce(eb.goals, 0) ELSE eb.goals END AS player_b_goals,
    CASE WHEN m.detail_captured_at IS NOT NULL THEN coalesce(eb.assists, 0) ELSE eb.assists END AS player_b_assists,
    CASE
        WHEN m.detail_captured_at IS NOT NULL AND coalesce(eb.penalties_without_stated_minutes, 0) = 0
        THEN coalesce(eb.stated_penalty_minutes, 0)
        ELSE eb.stated_penalty_minutes
    END AS player_b_stated_penalty_minutes,
    coalesce(eb.penalties_without_stated_minutes, 0) AS player_b_penalties_without_stated_minutes
FROM lineup_players a
JOIN lineup_players b
    ON a.match_id = b.match_id
   AND a.player_id < b.player_id
JOIN matches m ON m.match_id = a.match_id
LEFT JOIN seasons s ON s.season_id = m.season_id
LEFT JOIN player_match_event_totals ea
    ON ea.match_id = a.match_id AND ea.player_id = a.player_id
LEFT JOIN player_match_event_totals eb
    ON eb.match_id = b.match_id AND eb.player_id = b.player_id;

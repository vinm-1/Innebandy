-- Head-to-head queries over the staging database.
--
-- player_head_to_head keeps one row per match and unordered player pair.
-- The lower iBIS player id is player_a. relation is 'opponents' or 'same_team'.
-- Goals, assists and stated penalty minutes come from match events.
-- They are 0 when the match detail was stored and that player had no such event.
-- They stay NULL when the detail page was not fetched.
-- Penalty minutes count only lengths written in the penalty name ("2 min", "2+2 min").

-- Players on opposing teams.
SELECT
    match_datetime,
    season_name,
    competition_name,
    player_a_name,
    player_a_team_name,
    player_a_shirt,
    player_b_name,
    player_b_team_name,
    player_b_shirt,
    goals_home,
    goals_away,
    player_a_goals,
    player_a_assists,
    player_a_stated_penalty_minutes,
    player_b_goals,
    player_b_assists,
    player_b_stated_penalty_minutes
FROM player_head_to_head
WHERE player_a_id = 114860
  AND player_b_id = 378994
  AND relation = 'opponents'
ORDER BY match_datetime;

-- Same pair, either order, same team or opponents.
-- SELECT * FROM player_head_to_head
-- WHERE (player_a_id = :low AND player_b_id = :high)
--    OR (player_a_id = :high AND player_b_id = :low);

-- Meetings between two clubs in one competition, including matches
-- whose lineups were not published. Names are matched with the stored text.
SELECT
    match_id,
    match_datetime,
    home_team_name,
    away_team_name,
    goals_home,
    goals_away,
    detail_captured_at IS NOT NULL AS events_fetched
FROM matches
WHERE competition_id = 25003
  AND (
        (home_team_name = 'Sundsvall City IBC' AND away_team_name LIKE 'Ersboda%')
     OR (away_team_name = 'Sundsvall City IBC' AND home_team_name LIKE 'Ersboda%')
  )
ORDER BY match_datetime;

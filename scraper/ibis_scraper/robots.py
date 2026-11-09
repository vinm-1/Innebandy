"""Minimal robots.txt reader.

The public stats site publishes https://stats.innebandy.se/robots.txt.
The JSON API host does not currently serve a robots file (the site root
returns 503 and /v2/robots.txt returns 404). Callers still have to pass
the stats-site document and skip work when this user agent is disallowed.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RuleGroup:
    agents: list[str] = field(default_factory=list)
    allow: list[str] = field(default_factory=list)
    disallow: list[str] = field(default_factory=list)
    crawl_delay: float | None = None


def parse_robots(text: str) -> list[RuleGroup]:
    groups: list[RuleGroup] = []
    current: RuleGroup | None = None
    seen_rule = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if current is None or seen_rule:
                current = RuleGroup()
                groups.append(current)
                seen_rule = False
            current.agents.append(value)
            continue
        if current is None:
            continue
        seen_rule = True
        if key == "allow":
            current.allow.append(value)
        elif key == "disallow":
            current.disallow.append(value)
        elif key == "crawl-delay":
            try:
                current.crawl_delay = float(value)
            except ValueError:
                current.crawl_delay = None
    return groups


def _agent_matches(group: RuleGroup, user_agent: str) -> bool:
    ua = user_agent.lower()
    for agent in group.agents:
        token = agent.lower()
        if token == "*":
            return True
        if token and token in ua:
            return True
    return False


def matching_group(groups: list[RuleGroup], user_agent: str) -> RuleGroup | None:
    specific = [g for g in groups if _agent_matches(g, user_agent) and not any(a.strip() == "*" for a in g.agents)]
    if specific:
        # Merge specific groups that name this agent. The published file uses one group.
        merged = RuleGroup(agents=[user_agent])
        for group in specific:
            merged.allow.extend(group.allow)
            merged.disallow.extend(group.disallow)
            if group.crawl_delay is not None:
                merged.crawl_delay = group.crawl_delay
        return merged
    for group in groups:
        if any(a.strip() == "*" for a in group.agents):
            return group
    return None


def path_allowed(group: RuleGroup | None, path: str) -> bool:
    if group is None:
        return True
    if not path.startswith("/"):
        path = "/" + path
    best_len = -1
    allowed = True
    for rule, is_allow in [(r, False) for r in group.disallow] + [(r, True) for r in group.allow]:
        if rule == "":
            continue
        if path.startswith(rule) and len(rule) >= best_len:
            # Equal-length Allow wins over Disallow, matching the common interpretation.
            if len(rule) == best_len and allowed and not is_allow:
                continue
            best_len = len(rule)
            allowed = is_allow
    return allowed


def crawl_delay_seconds(group: RuleGroup | None) -> float | None:
    if group is None:
        return None
    return group.crawl_delay

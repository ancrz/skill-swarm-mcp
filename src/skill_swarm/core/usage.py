"""Skill usage tracking — measures real utility of installed skills."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from skill_swarm.config import settings
from skill_swarm.models import SkillUsageStats

logger = logging.getLogger("skill-swarm.usage")

_USAGE_FILE = ".usage.json"


def _usage_path() -> Path:
    return settings.skills_dir / _USAGE_FILE


def _load_all() -> dict[str, dict]:
    """Load all usage stats from disk."""
    path = _usage_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to load usage stats: %s", e)
        return {}


def _save_all(data: dict[str, dict]) -> None:
    """Save all usage stats to disk."""
    settings.skills_dir.mkdir(parents=True, exist_ok=True)
    _usage_path().write_text(
        json.dumps(data, indent=2, default=str),
        encoding="utf-8",
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_event(skill_name: str, event_type: str) -> None:
    """Record a usage event for a skill.

    event_type: "match", "cherry_pick", "full_read", "search"
    """
    data = _load_all()
    entry = data.get(skill_name, {})

    if event_type == "match":
        entry["match_hits"] = entry.get("match_hits", 0) + 1
    elif event_type == "cherry_pick":
        entry["cherry_pick_count"] = entry.get("cherry_pick_count", 0) + 1
    elif event_type == "full_read":
        entry["full_read_count"] = entry.get("full_read_count", 0) + 1
    elif event_type == "search":
        entry["match_hits"] = entry.get("match_hits", 0) + 1

    entry["last_used"] = _now_iso()
    entry["last_usage_type"] = event_type

    if "installed_at" not in entry:
        entry["installed_at"] = _now_iso()

    data[skill_name] = entry
    _save_all(data)


def mark_installed(skill_name: str) -> None:
    """Mark a skill as installed (sets installed_at if not already set)."""
    data = _load_all()
    entry = data.get(skill_name, {})
    if "installed_at" not in entry:
        entry["installed_at"] = _now_iso()
    data[skill_name] = entry
    _save_all(data)


def get_stats(skill_name: str) -> SkillUsageStats:
    """Get usage stats for a specific skill."""
    data = _load_all()
    entry = data.get(skill_name, {})
    return SkillUsageStats(**entry)


def get_all_stats() -> dict[str, SkillUsageStats]:
    """Get usage stats for all tracked skills."""
    data = _load_all()
    return {name: SkillUsageStats(**entry) for name, entry in data.items()}


def remove_stats(skill_name: str) -> None:
    """Remove usage tracking for an uninstalled skill."""
    data = _load_all()
    data.pop(skill_name, None)
    _save_all(data)


def get_dead_skills() -> list[str]:
    """Find skills that are installed but never used."""
    all_stats = get_all_stats()
    return [name for name, stats in all_stats.items() if stats.primary_usage == "dead"]

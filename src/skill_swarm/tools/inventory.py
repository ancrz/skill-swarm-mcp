"""Inventory tools: list, match, and inspect installed skills."""

from skill_swarm.config import settings
from skill_swarm.core.installer import load_manifest
from skill_swarm.core.matcher import match_skills_local
from skill_swarm.core.usage import get_all_stats, get_dead_skills, get_stats, record_event


def list_skills(agent: str = "all") -> dict:
    """List all installed skills with their metadata, symlink status, and usage stats.

    Args:
        agent: Filter by agent ("claude", "gemini") or "all" for everything.

    Returns:
        Dictionary with skills list, symlink health status, and usage.
    """
    manifest = load_manifest()
    all_usage = get_all_stats()

    skills_list = []
    for name, info in manifest.skills.items():
        # Check symlink health
        symlink_status: dict[str, str] = {}
        for agent_name, agent_dir in settings.agent_dirs.items():
            if agent != "all" and agent_name != agent:
                continue
            link = agent_dir / name  # directory-level symlink
            if link.is_symlink():
                skill_file = link / "skill.md"
                if skill_file.exists():
                    symlink_status[agent_name] = "ok"
                else:
                    symlink_status[agent_name] = "broken"
            elif link.exists():
                symlink_status[agent_name] = "directory (not symlink)"
            else:
                symlink_status[agent_name] = "missing"

        # Usage stats
        usage = all_usage.get(name)
        usage_info = None
        if usage:
            usage_info = {
                "primary_usage": usage.primary_usage,
                "match_hits": usage.match_hits,
                "cherry_pick_count": usage.cherry_pick_count,
                "full_read_count": usage.full_read_count,
                "last_used": usage.last_used,
            }

        skills_list.append({
            "name": info.name,
            "description": info.description,
            "version": info.version,
            "source": info.source,
            "agents": info.agents,
            "symlinks": symlink_status,
            "usage": usage_info,
        })

    return {
        "total": len(skills_list),
        "skills_dir": str(settings.skills_dir),
        "dead_skills": get_dead_skills(),
        "skills": skills_list,
    }


def match_skills(task_description: str, threshold: float = 0.05) -> list[dict]:
    """Find installed skills that match a task description.

    Uses BM25F + multi-signal scoring on skill names, tags, and descriptions.

    Args:
        task_description: What you want to accomplish
        threshold: Minimum relevance score (0.0-1.0)

    Returns:
        Skills sorted by relevance with match percentage.
    """
    results = match_skills_local(task_description, threshold)

    # Track usage for matched skills
    for r in results:
        record_event(r.name, "match")

    return [
        {
            "name": r.name,
            "description": r.description,
            "relevance_pct": round(r.relevance * 100, 1),
            "source": r.source,
            "tags": r.tags,
        }
        for r in results
    ]


def get_skill_info(name: str) -> dict:
    """Get full metadata and content of an installed skill.

    Args:
        name: Skill name to inspect.

    Returns:
        Skill metadata, content, symlink status, and usage stats.
    """
    skill_path = settings.skill_path(name)

    if not skill_path.exists():
        return {"error": f"Skill '{name}' not found at {skill_path}"}

    content = skill_path.read_text(encoding="utf-8")

    # Track full read
    record_event(name, "full_read")

    # Check manifest
    manifest = load_manifest()
    info = manifest.skills.get(name)

    # Check symlinks (directory-level)
    symlinks: dict[str, str] = {}
    for agent_name, agent_dir in settings.agent_dirs.items():
        link = agent_dir / name
        if link.is_symlink() and (link / "skill.md").exists():
            symlinks[agent_name] = "linked"
        elif link.exists():
            symlinks[agent_name] = "directory (not symlink)"
        else:
            symlinks[agent_name] = "not linked"

    # Usage stats
    usage = get_stats(name)

    result = {
        "name": name,
        "path": str(skill_path),
        "size_bytes": skill_path.stat().st_size,
        "content": content,
        "symlinks": symlinks,
        "usage": {
            "primary_usage": usage.primary_usage,
            "match_hits": usage.match_hits,
            "cherry_pick_count": usage.cherry_pick_count,
            "full_read_count": usage.full_read_count,
            "last_used": usage.last_used,
        },
    }

    if info:
        result["description"] = info.description
        result["version"] = info.version
        result["tags"] = info.tags
        result["source"] = info.source

    return result

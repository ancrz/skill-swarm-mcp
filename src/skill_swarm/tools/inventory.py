"""Inventory tools: list, match, and inspect installed skills."""

from skill_swarm.config import settings
from skill_swarm.core.installer import load_manifest
from skill_swarm.core.matcher import match_skills_local
from skill_swarm.core.normalizer import normalize_query
from skill_swarm.core.usage import get_all_stats, get_dead_skills, get_stats, record_event


def list_skills(agent: str = "all") -> dict:
    """List all installed skills with their metadata, symlink status, and usage stats.

    Args:
        agent: Filter by client ("claude", "agy", "codex") or "all".

    Returns:
        Dictionary with skills list, symlink health status, and usage.
    """
    manifest = load_manifest()
    all_usage = get_all_stats()

    skills_list = []
    for name, info in manifest.skills.items():
        # Check symlink health
        symlink_status: dict[str, str] = {}
        clients = [*settings.agent_dirs.keys(), *sorted(settings.native_agents)]
        requested_agent = settings.agent_aliases.get(agent, agent)
        for agent_name in clients:
            if requested_agent != "all" and agent_name != requested_agent:
                continue
            if agent_name in settings.native_agents:
                symlink_status[agent_name] = "native" if settings.skill_path(name).exists() else "missing"
                continue
            statuses: list[str] = []
            for agent_dir in settings.targets_for_agent(agent_name):
                link = agent_dir / name
                if link.is_symlink():
                    statuses.append("ok" if (link / "SKILL.md").exists() else "broken")
                elif link.exists():
                    statuses.append("unmanaged")
                else:
                    statuses.append("missing")
            symlink_status[agent_name] = "ok" if statuses and all(s == "ok" for s in statuses) else ",".join(statuses)

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
    task_description = normalize_query(task_description)
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
    for agent_name in [*settings.agent_dirs.keys(), *sorted(settings.native_agents)]:
        if agent_name in settings.native_agents:
            symlinks[agent_name] = "native" if skill_path.exists() else "missing"
            continue
        links = [agent_dir / name for agent_dir in settings.targets_for_agent(agent_name)]
        if links and all(link.is_symlink() and (link / "SKILL.md").exists() for link in links):
            symlinks[agent_name] = "linked"
        elif any(link.exists() and not link.is_symlink() for link in links):
            symlinks[agent_name] = "unmanaged directory"
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

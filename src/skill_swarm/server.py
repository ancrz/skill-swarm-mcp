"""skill-swarm MCP server.

Provides 8 tools for AI agent skill management:
- search_skills: Find skills locally and in 5 remote registries with trust scoring
- match_skills: BM25F + multi-signal scoring of installed skills against a task
- install_skill: Download, security-scan, trust-check, and install globally
- uninstall_skill: Remove a skill and all agent symlinks
- list_skills: Inventory with health status, usage stats, and dead skill detection
- get_skill_info: Full metadata, content, and usage stats of a skill
- cherry_pick_context: Extract specific sections from a skill (partial context)
- skill_health: Usage analytics and dead skill detection
"""

import json
import logging

from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

mcp = FastMCP(
    "skill-swarm",
    instructions=(
        "Skill Swarm manages AI agent skills globally. "
        "Use match_skills first to check if a local skill exists for a task. "
        "If no match, use search_skills with scope='remote' to find one in 5 registries "
        "(Official MCP Registry, Smithery, Glama, GitHub). "
        "Results include trust scores based on git-quality signals (stars, license, recency). "
        "Use cherry_pick_context to extract only the sections you need."
    ),
)


@mcp.tool()
async def search_skills(query: str, scope: str = "all", limit: int = 5) -> str:
    """Search for skills across 5 registries with trust scoring.

    Searches: Official MCP Registry, Smithery, Glama.ai, GitHub.
    Results include trust_score (0-1) computed from git signals.

    Args:
        query: What you want to do (e.g. "parse PDF", "deploy docker")
        scope: Where to search - "local", "remote", or "all"
        limit: Maximum number of results
    """
    from skill_swarm.tools.search import search_skills as _search

    results = await _search(query=query, scope=scope, limit=limit)
    return json.dumps([r.model_dump() for r in results], indent=2)


@mcp.tool()
async def match_skills(task_description: str, threshold: float = 0.05) -> str:
    """Find installed skills matching a task using BM25F + multi-signal scoring.

    7 signals: exact match, prefix, phrase, BM25F, Jaccard tags, fuzzy name, fuzzy description.

    Args:
        task_description: What you want to accomplish
        threshold: Minimum relevance score 0.0-1.0 (default 0.05 = 5%)
    """
    from skill_swarm.tools.inventory import match_skills as _match

    results = _match(task_description=task_description, threshold=threshold)
    return json.dumps(results, indent=2)


@mcp.tool()
async def install_skill(name: str, source: str, agents: str = "claude,gemini") -> str:
    """Download, security-scan, and install a skill globally with agent symlinks.

    Pipeline: download -> security scan -> atomic install -> symlink -> manifest.
    Purges search cache after install (inventory changed).

    Args:
        name: Skill identifier (e.g. "pdf-parser")
        source: URL (markdown, zip, GitHub repo) or local path
        agents: Comma-separated agent names to link (default: "claude,gemini")
    """
    from skill_swarm.tools.install import install_skill as _install

    agent_list = [a.strip() for a in agents.split(",") if a.strip()]
    result = await _install(name=name, source=source, agents=agent_list)
    return json.dumps(result.model_dump(), indent=2)


@mcp.tool()
async def uninstall_skill(name: str) -> str:
    """Remove a skill, all agent symlinks, and usage tracking data.

    Args:
        name: Skill name to remove
    """
    from skill_swarm.tools.install import uninstall_skill as _uninstall

    result = await _uninstall(name=name)
    return json.dumps(result.model_dump(), indent=2)


@mcp.tool()
async def list_skills(agent: str = "all") -> str:
    """List installed skills with metadata, symlink health, usage stats, and dead skills.

    Args:
        agent: Filter by agent name or "all"
    """
    from skill_swarm.tools.inventory import list_skills as _list

    result = _list(agent=agent)
    return json.dumps(result, indent=2)


@mcp.tool()
async def get_skill_info(name: str) -> str:
    """Get full metadata, content, symlink status, and usage stats of an installed skill.

    Counts as a full_read in usage tracking.

    Args:
        name: Skill name to inspect
    """
    from skill_swarm.tools.inventory import get_skill_info as _info

    result = _info(name=name)
    return json.dumps(result, indent=2)


@mcp.tool()
async def cherry_pick_context(skill_name: str, sections: str) -> str:
    """Extract specific sections from a skill (like git cherry-pick for context).

    Instead of loading an entire skill, extract only the sections relevant
    to your current task. Sections are matched against H2/H3 markdown headers.
    Counts as cherry_pick in usage tracking.

    Args:
        skill_name: Name of the installed skill
        sections: Comma-separated section names (e.g. "Rollback,Health Check")
    """
    from skill_swarm.tools.cherry_pick import cherry_pick_context as _pick

    section_list = [s.strip() for s in sections.split(",") if s.strip()]
    result = _pick(skill_name=skill_name, sections=section_list)
    return json.dumps(result, indent=2)


@mcp.tool()
async def skill_health() -> str:
    """Usage analytics: which skills are active, cherry-pick-only, or dead.

    Returns per-skill usage classification and identifies dead skills
    (installed but never matched or read).
    """
    from skill_swarm.core.cache import cache_stats
    from skill_swarm.core.usage import get_all_stats, get_dead_skills

    all_stats = get_all_stats()
    dead = get_dead_skills()

    health = {
        "total_tracked": len(all_stats),
        "dead_skills": dead,
        "cache": cache_stats(),
        "skills": {
            name: {
                "primary_usage": stats.primary_usage,
                "match_hits": stats.match_hits,
                "cherry_pick_count": stats.cherry_pick_count,
                "full_read_count": stats.full_read_count,
                "last_used": stats.last_used,
                "installed_at": stats.installed_at,
            }
            for name, stats in all_stats.items()
        },
    }
    return json.dumps(health, indent=2)


def main():
    """Entry point for the MCP server."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

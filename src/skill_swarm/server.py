"""skill-swarm MCP server.

Consolidated 3-Tool Surface (AEP Pattern) + 100% Backward-Compatible Wrappers:
Core Tools:
- skill_discover: Unified discovery router (capabilities, match, search)
- skill_manage: Unified lifecycle control plane (list, info, install, uninstall, update, health, audit)
- cherry_pick_context: Context-efficient markdown section extraction
"""

import json
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from skill_swarm.core.installer import migrate_legacy_skills_dir, normalize_skill_filenames

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

_INSTRUCTIONS = (
    "Skill Swarm manages AI agent skills globally using AEP resilience primitives. "
    "FIRST CALL RECOMMENDED: `skill_discover(action='capabilities')` returns an autonomy manifest. "
    "For finding skills: `skill_discover(action='discover', query='...')` checks local skills and falls back to remote registries. "
    "For lifecycle management: `skill_manage(action='list'|'info'|'install'|'uninstall'|'update'|'health'|'audit')`. "
    "For token-efficient context: `cherry_pick_context(skill_name, sections)` extracts specific markdown sections."
)

mcp = FastMCP("skill-swarm", instructions=_INSTRUCTIONS)

# Startup migrations
_legacy_migrated = migrate_legacy_skills_dir()
_migrated = normalize_skill_filenames()
if _legacy_migrated:
    logging.getLogger("skill-swarm").info("Startup migration: moved %d entries to ~/.agents/skills", _legacy_migrated)
if _migrated:
    logging.getLogger("skill-swarm").info("Startup migration: renamed %d skill files to SKILL.md", _migrated)


# ==============================================================================
# CORE TOOL 1: skill_discover (Capabilities + Semantic Match + Remote Search)
# ==============================================================================
@mcp.tool()
async def skill_discover(
    action: str = "discover",
    query: str | None = None,
    limit: int = 10,
    scope: str = "all",
) -> str:
    """[DISCOVERY & ORIENTATION] Unified entry point for skill capabilities, matching, and search.

    action:
      - 'capabilities' (or 'manifest'): Returns self-describing autonomy manifest and system capabilities.
      - 'discover' (default): Automatically matches locally first; falls back to remote search if no hits.
      - 'match': Fast BM25F + multi-signal scoring of installed skills against query.
      - 'search': Multi-registry search (Skills.sh, Official MCP Registry, Smithery, Glama, GitHub).

    Args:
        action: 'capabilities', 'discover', 'match', or 'search'.
        query: Keywords to search or match (e.g. 'docker deployment', 'pdf parser').
        limit: Maximum results to return.
        scope: Search scope ('local', 'remote', or 'all').
    """
    act = (action or "discover").strip().lower()

    if act in ("capabilities", "manifest"):
        manifest = {
            "status": "OK",
            "server": "skill-swarm",
            "version": "1.2.0",
            "resilience_specs": {
                "SPEC-1": "Resilient storage with pre-flight writability and volatile RAM fallback",
                "SPEC-2": "Tamper-evident SHA-256 chained audit ledger and credential masking",
                "SPEC-3": "Topos Integrity Circuit Breaker (Anti-co-dependency DAG)",
                "SPEC-4": "User-space Landlock LSM sandboxing probe (pure ctypes libc.so.6)",
                "SPEC-5": "Multidimensional skill health and trust scoring (Q, R, E, T, Security Veto)",
            },
            "tools": {
                "skill_discover": "Orientation, capabilities manifest, local matching, remote search",
                "skill_manage": "Lifecycle control plane (list, info, install, uninstall, update, health, audit)",
                "cherry_pick_context": "Extract targeted markdown sections to minimize token usage",
            },
            "registries": ["Skills.sh", "MCP Registry", "Smithery", "Glama.ai", "GitHub"],
        }
        return json.dumps(manifest, indent=2)

    q = (query or "").strip()
    if not q:
        return json.dumps({"status": "PARAM_ERROR", "error": "Query required for discovery/match/search."}, indent=2)

    if act == "match":
        from skill_swarm.tools.inventory import match_skills as _match
        return json.dumps(_match(task_description=q, threshold=0.05)[:limit], indent=2)

    elif act == "search":
        from skill_swarm.tools.search import search_skills as _search
        results = await _search(query=q, scope=scope, limit=limit)
        return json.dumps([r.model_dump() for r in results], indent=2)

    elif act == "discover":
        from skill_swarm.tools.inventory import match_skills as _match
        local_matches = _match(task_description=q, threshold=0.05)
        if local_matches:
            return json.dumps({"status": "OK", "source": "local", "count": len(local_matches), "results": local_matches[:limit]}, indent=2)

        from skill_swarm.tools.search import search_skills as _search
        remote_results = await _search(query=q, scope="remote", limit=limit)
        return json.dumps({
            "status": "OK",
            "source": "remote",
            "count": len(remote_results),
            "results": [r.model_dump() for r in remote_results],
        }, indent=2)

    return json.dumps({"status": "INVALID_ACTION", "error": f"Unknown action '{action}'"}, indent=2)


# ==============================================================================
# CORE TOOL 2: skill_manage (Lifecycle Control Plane: Inventory, Health, Audit)
# ==============================================================================
@mcp.tool()
async def skill_manage(
    action: str,
    name: str | None = None,
    source: str | None = None,
    agents: str = "claude,agy,codex",
    agent_filter: str = "all",
) -> str:
    """[LIFECYCLE CONTROL PLANE] Unified management for skill inventory, installation, health, and audit.

    action:
      - 'list': List installed skills with symlink health, usage, and multidimensional health score.
      - 'info': Inspect full metadata, SKILL.md content, and diagnostics for skill 'name'.
      - 'install': Atomic installation of skill 'name' from 'source' with Landlock and Topos validation.
      - 'uninstall': Remove skill 'name' and purge managed symlinks.
      - 'update': Refresh skill 'name' from original source with atomic replacement.
      - 'health': Multidimensional confidence scoring (Q, R, E, T, Security Veto) for fleet or 'name'.
      - 'audit': Verify cryptographic SHA-256 chained audit ledger.

    Args:
        action: 'list', 'info', 'install', 'uninstall', 'update', 'health', or 'audit'.
        name: Skill name (required for info, install, uninstall, update).
        source: Source URL, git repo, or path (required for install).
        agents: Target agent clients for installation symlinks.
        agent_filter: Filter for 'list' action ('all', 'claude', 'agy', 'codex').
    """
    act = (action or "").strip().lower()

    if act == "list":
        from skill_swarm.tools.inventory import list_skills as _list
        return json.dumps(_list(agent=agent_filter), indent=2)

    elif act == "info":
        if not name:
            return json.dumps({"status": "PARAM_ERROR", "error": "Action 'info' requires 'name'"}, indent=2)
        from skill_swarm.tools.inventory import get_skill_info as _info
        return json.dumps(_info(name=name), indent=2)

    elif act == "install":
        if not name or not source:
            return json.dumps({"status": "PARAM_ERROR", "error": "Action 'install' requires 'name' and 'source'"}, indent=2)
        from skill_swarm.tools.install import install_skill as _install
        agent_list = [a.strip() for a in agents.split(",") if a.strip()]
        res = await _install(name=name, source=source, agents=agent_list)
        return json.dumps(res.model_dump(), indent=2)

    elif act == "uninstall":
        if not name:
            return json.dumps({"status": "PARAM_ERROR", "error": "Action 'uninstall' requires 'name'"}, indent=2)
        from skill_swarm.tools.install import uninstall_skill as _uninstall
        res = await _uninstall(name=name)
        return json.dumps(res.model_dump(), indent=2)

    elif act == "update":
        if not name:
            return json.dumps({"status": "PARAM_ERROR", "error": "Action 'update' requires 'name'"}, indent=2)
        from skill_swarm.tools.install import update_skill as _update
        res = await _update(name=name)
        return json.dumps(res.model_dump(), indent=2)

    elif act == "health":
        from skill_swarm.tools.inventory import get_skill_health as _health
        return json.dumps(_health(name=name), indent=2)

    elif act == "audit":
        from skill_swarm.tools.inventory import verify_audit as _verify
        return json.dumps(_verify(), indent=2)

    return json.dumps({"status": "INVALID_ACTION", "error": f"Unknown action '{action}'"}, indent=2)


# ==============================================================================
# CORE TOOL 3: cherry_pick_context (Token-Efficient Reader)
# ==============================================================================
@mcp.tool()
async def cherry_pick_context(skill_name: str, sections: list[str]) -> str:
    """Extract specific sections from an installed skill markdown without loading full context."""
    from skill_swarm.tools.cherry_pick import cherry_pick_context as _pick
    return json.dumps(_pick(skill_name=skill_name, sections=sections), indent=2)


# ==============================================================================
# BACKWARD COMPATIBILITY WRAPPERS (Forward to skill_discover / skill_manage)
# ==============================================================================
@mcp.tool()
async def search_skills(query: str, scope: str = "all", limit: int = 5) -> str:
    """[COMPAT] Forwarder to skill_discover(action='search')."""
    return await skill_discover(action="search", query=query, scope=scope, limit=limit)

@mcp.tool()
async def match_skills(task_description: str, threshold: float = 0.05) -> str:
    """[COMPAT] Forwarder to skill_discover(action='match')."""
    return await skill_discover(action="match", query=task_description, limit=10)

@mcp.tool()
async def install_skill(name: str, source: str, agents: str = "claude,agy,codex") -> str:
    """[COMPAT] Forwarder to skill_manage(action='install')."""
    return await skill_manage(action="install", name=name, source=source, agents=agents)

@mcp.tool()
async def uninstall_skill(name: str) -> str:
    """[COMPAT] Forwarder to skill_manage(action='uninstall')."""
    return await skill_manage(action="uninstall", name=name)

@mcp.tool()
async def update_skill(name: str) -> str:
    """[COMPAT] Forwarder to skill_manage(action='update')."""
    return await skill_manage(action="update", name=name)

@mcp.tool()
async def list_skills(agent: str = "all") -> str:
    """[COMPAT] Forwarder to skill_manage(action='list')."""
    return await skill_manage(action="list", agent_filter=agent)

@mcp.tool()
async def get_skill_info(name: str) -> str:
    """[COMPAT] Forwarder to skill_manage(action='info')."""
    return await skill_manage(action="info", name=name)

@mcp.tool()
async def skill_health(name: str | None = None) -> str:
    """[COMPAT] Forwarder to skill_manage(action='health')."""
    return await skill_manage(action="health", name=name)

@mcp.tool()
async def verify_audit() -> str:
    """[COMPAT] Forwarder to skill_manage(action='audit')."""
    return await skill_manage(action="audit")


def main():
    mcp.run(transport="stdio")

if __name__ == "__main__":
    main()

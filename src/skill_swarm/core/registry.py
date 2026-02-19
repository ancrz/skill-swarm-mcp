"""Registry clients for skill discovery from 5 remote sources.

Sources (by trust level):
1. Official MCP Registry (registry.modelcontextprotocol.io) — 0.85
2. Skills.sh / Vercel — 0.75
3. Smithery (registry.smithery.ai) — 0.70
4. Glama.ai (glama.ai) — 0.65
5. GitHub (api.github.com) — 0.50
"""

import asyncio
import logging

import httpx

from skill_swarm.config import settings
from skill_swarm.core.cache import get_cached, set_cached
from skill_swarm.core.trust import evaluate_github_repo, quick_trust_from_registry
from skill_swarm.models import SearchResult

logger = logging.getLogger("skill-swarm.registry")


# ─── 1. Official MCP Registry ──────────────────────────────────────────────

async def search_mcp_registry(query: str, limit: int = 5) -> list[SearchResult]:
    """Search the official MCP Registry (Anthropic / Agentic AI Foundation)."""
    cache_key = ("search", "mcp_registry", query, str(limit))
    cached = get_cached(*cache_key, ttl=settings.cache_search_ttl)
    if cached:
        return [SearchResult.model_validate(r) for r in cached]

    try:
        async with httpx.AsyncClient(timeout=settings.search_timeout) as client:
            resp = await client.get(
                settings.mcp_registry_url,
                params={"search": query, "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()

        results: list[SearchResult] = []
        for entry in data.get("servers", [])[:limit]:
            server = entry.get("server", entry)
            repo_url = ""
            repo_data = server.get("repository", {})
            if isinstance(repo_data, dict):
                repo_url = repo_data.get("url", "")

            results.append(SearchResult(
                name=server.get("name", "unknown"),
                description=server.get("description", ""),
                source="mcp_registry",
                url=repo_url,
                relevance=0.85,
                tags=[],
            ))

        set_cached(*cache_key, payload=[r.model_dump() for r in results])
        logger.info("MCP Registry: found %d results for '%s'", len(results), query)
        return results

    except Exception as e:
        logger.warning("MCP Registry search failed: %s", e)
        return []


# ─── 2. Smithery ───────────────────────────────────────────────────────────

async def search_smithery(query: str, limit: int = 5) -> list[SearchResult]:
    """Search Smithery.ai registry for MCP servers/skills."""
    cache_key = ("search", "smithery", query, str(limit))
    cached = get_cached(*cache_key, ttl=settings.cache_search_ttl)
    if cached:
        return [SearchResult.model_validate(r) for r in cached]

    try:
        async with httpx.AsyncClient(timeout=settings.search_timeout) as client:
            resp = await client.get(
                settings.smithery_api_url,
                params={"q": query, "pageSize": limit},
            )
            resp.raise_for_status()
            data = resp.json()

        results: list[SearchResult] = []
        servers = data.get("servers", []) if isinstance(data, dict) else data

        for server in servers[:limit]:
            results.append(SearchResult(
                name=server.get("qualifiedName", server.get("name", "unknown")),
                description=server.get("description", ""),
                source="smithery",
                url=server.get("homepage", server.get("url", "")),
                relevance=0.70,
                tags=server.get("tags", []),
            ))

        set_cached(*cache_key, payload=[r.model_dump() for r in results])
        logger.info("Smithery: found %d results for '%s'", len(results), query)
        return results

    except Exception as e:
        logger.warning("Smithery search failed: %s", e)
        return []


# ─── 3. Glama.ai ──────────────────────────────────────────────────────────

async def search_glama(query: str, limit: int = 5) -> list[SearchResult]:
    """Search Glama.ai MCP server registry."""
    cache_key = ("search", "glama", query, str(limit))
    cached = get_cached(*cache_key, ttl=settings.cache_search_ttl)
    if cached:
        return [SearchResult.model_validate(r) for r in cached]

    try:
        async with httpx.AsyncClient(timeout=settings.search_timeout) as client:
            resp = await client.get(
                settings.glama_api_url,
                params={"query": query, "first": limit},
            )
            resp.raise_for_status()
            data = resp.json()

        results: list[SearchResult] = []
        servers = data.get("servers", data.get("data", []))
        if isinstance(servers, list):
            for server in servers[:limit]:
                results.append(SearchResult(
                    name=server.get("slug", server.get("name", "unknown")),
                    description=server.get("description", ""),
                    source="glama",
                    url=server.get("url", server.get("homepage", "")),
                    relevance=0.65,
                    tags=server.get("attributes", []),
                ))

        set_cached(*cache_key, payload=[r.model_dump() for r in results])
        logger.info("Glama: found %d results for '%s'", len(results), query)
        return results

    except Exception as e:
        logger.warning("Glama search failed: %s", e)
        return []


# ─── 4. GitHub ─────────────────────────────────────────────────────────────

async def search_github(query: str, limit: int = 5) -> list[SearchResult]:
    """Search GitHub for skill/MCP server repositories.

    Uses token auth if configured (30 req/min vs 10 unauthenticated).
    """
    cache_key = ("search", "github", query, str(limit))
    cached = get_cached(*cache_key, ttl=settings.cache_search_ttl)
    if cached:
        return [SearchResult.model_validate(r) for r in cached]

    try:
        search_query = f"{query} skill OR mcp-server in:name,description"
        headers = {"Accept": "application/vnd.github.v3+json"}
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"

        async with httpx.AsyncClient(timeout=settings.search_timeout) as client:
            resp = await client.get(
                "https://api.github.com/search/repositories",
                params={"q": search_query, "sort": "stars", "per_page": limit},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        results: list[SearchResult] = []
        for repo in data.get("items", [])[:limit]:
            results.append(SearchResult(
                name=repo.get("full_name", repo.get("name", "unknown")),
                description=repo.get("description", "") or "",
                source="github",
                url=repo.get("html_url", ""),
                relevance=0.50,
                tags=repo.get("topics", []),
            ))

        set_cached(*cache_key, payload=[r.model_dump() for r in results])
        logger.info("GitHub: found %d results for '%s'", len(results), query)
        return results

    except httpx.HTTPStatusError as e:
        logger.warning("GitHub API error %d: %s", e.response.status_code, e)
        return []
    except Exception as e:
        logger.warning("GitHub search failed: %s", e)
        return []


# ─── 5. Combined Search ───────────────────────────────────────────────────

async def search_remote(
    query: str,
    limit: int = 5,
    with_trust: bool = True,
) -> list[SearchResult]:
    """Search all remote registries in parallel, deduplicate, and optionally score trust.

    Order: MCP Registry → Smithery → Glama → GitHub (by trust level).
    """
    # Query all registries in parallel
    tasks = [
        search_mcp_registry(query, limit),
        search_smithery(query, limit),
        search_glama(query, limit),
        search_github(query, limit),
    ]
    all_results_lists = await asyncio.gather(*tasks, return_exceptions=True)

    # Flatten results (skip exceptions)
    all_results: list[SearchResult] = []
    for result_or_error in all_results_lists:
        if isinstance(result_or_error, list):
            all_results.extend(result_or_error)

    # Deduplicate by normalized name + URL
    seen: set[str] = set()
    unique: list[SearchResult] = []
    for r in sorted(all_results, key=lambda x: x.relevance, reverse=True):
        dedup_key = _normalize_name(r.name)
        if dedup_key not in seen:
            seen.add(dedup_key)
            unique.append(r)

    # Optionally evaluate trust scores for GitHub results
    if with_trust:
        for r in unique:
            if r.source == "github" and r.url and "github.com" in r.url:
                trust = await evaluate_github_repo(r.url)
                r.trust = trust
                # Adjust relevance based on trust
                r.relevance = round(r.relevance * 0.4 + trust.score * 0.6, 3)
            else:
                r.trust = quick_trust_from_registry(r.source)

    # Re-sort by relevance after trust adjustment
    unique.sort(key=lambda x: x.relevance, reverse=True)

    return unique[:limit]


def _normalize_name(name: str) -> str:
    """Normalize skill/server name for deduplication."""
    name = name.lower().strip()
    # Remove common prefixes/suffixes
    for prefix in ("mcp-", "mcp_", "@", "server-", "skill-"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    for suffix in ("-mcp", "-server", "-skill", ".skill.md"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    # Remove owner prefix (e.g., "user/repo" -> "repo")
    if "/" in name:
        name = name.split("/")[-1]
    return name

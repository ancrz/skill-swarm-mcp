"""Search tools: find skills locally and remotely."""

from skill_swarm.core.matcher import match_skills_local
from skill_swarm.core.registry import search_remote
from skill_swarm.core.usage import record_event
from skill_swarm.models import SearchResult


async def search_skills(
    query: str,
    scope: str = "all",
    limit: int = 5,
) -> list[SearchResult]:
    """Search for skills by what you need to accomplish.

    Args:
        query: What you want to do (e.g. "parse PDF", "deploy docker")
        scope: Where to search - "local" (installed only), "remote" (registries), or "all"
        limit: Maximum number of results

    Returns:
        List of matching skills sorted by relevance.
    """
    results: list[SearchResult] = []

    if scope in ("local", "all"):
        local = match_skills_local(query, threshold=0.2)
        # Track usage for local results
        for r in local:
            record_event(r.name, "search")
        results.extend(local)

    if scope in ("remote", "all"):
        remote = await search_remote(query, limit, with_trust=True)
        results.extend(remote)

    # Sort by relevance, local results tend to score higher
    results.sort(key=lambda r: r.relevance, reverse=True)
    return results[:limit]

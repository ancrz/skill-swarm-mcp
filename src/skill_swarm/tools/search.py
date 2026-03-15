"""Search tools: find skills locally and remotely."""

import logging

from skill_swarm.core.matcher import match_skills_local
from skill_swarm.core.normalizer import normalize_query
from skill_swarm.core.registry import search_remote_phased
from skill_swarm.core.usage import record_event
from skill_swarm.models import SearchResult

logger = logging.getLogger("skill-swarm.search")


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
    query = normalize_query(query)
    results: list[SearchResult] = []

    if scope in ("local", "all"):
        local = match_skills_local(query, threshold=0.2)
        # Track usage for local results
        for r in local:
            record_event(r.name, "search")
        results.extend(local)

    if scope in ("remote", "all"):
        remote, metadata = await search_remote_phased(query, limit, with_trust=True)
        results.extend(remote)
        logger.info(
            "Search '%s': phase1=%d (%s), phase2=%d (%s), total=%.0fms",
            query[:50],
            metadata.phase1_results,
            ",".join(metadata.phase1_sources),
            metadata.phase2_results,
            ",".join(metadata.phase2_sources) if metadata.phase2_sources else "skipped",
            metadata.total_duration_ms,
        )

    # Sort by relevance, local results tend to score higher
    results.sort(key=lambda r: r.relevance, reverse=True)
    return results[:limit]

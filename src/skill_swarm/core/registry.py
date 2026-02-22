"""Registry clients for skill discovery from 5 remote sources.

Sources (by priority):
1. Skills.sh / Vercel (npx skills find) — 0.90 (primary, actual agent skills)
2. Official MCP Registry (registry.modelcontextprotocol.io) — 0.85
3. Smithery (registry.smithery.ai) — 0.70
4. Glama.ai (glama.ai) — 0.65
5. GitHub (api.github.com) — 0.50
"""

import asyncio
import logging
import os
import re
import shutil

import httpx

from skill_swarm.config import settings
from skill_swarm.core.cache import get_cached, set_cached
from skill_swarm.core.trust import evaluate_github_repo, quick_trust_from_registry
from skill_swarm.models import SearchResult

logger = logging.getLogger("skill-swarm.registry")


# ─── 1. Skills.sh (Vercel) — Primary Registry ─────────────────────────────


async def search_skillssh(query: str, limit: int = 5) -> list[SearchResult]:
    """Search skills.sh via `npx skills find` CLI, with GitHub API fallback.

    Strategy A: subprocess `npx skills find <query>` — most accurate
    Strategy B: GitHub API topic search for SKILL.md repos — fallback
    """
    if not settings.skillssh_enabled:
        return []

    cache_key = ("search", "skillssh", query, str(limit))
    cached = get_cached(*cache_key, ttl=settings.cache_search_ttl)
    if cached:
        return [SearchResult.model_validate(r) for r in cached]

    # Strategy A: npx subprocess
    results = await _search_skillssh_npx(query, limit)

    # Strategy B: GitHub API fallback
    if not results and settings.skillssh_github_fallback:
        logger.info("skills.sh npx unavailable, falling back to GitHub topic search")
        results = await _search_skillssh_github(query, limit)

    if results:
        set_cached(*cache_key, payload=[r.model_dump() for r in results])

    logger.info("Skills.sh: found %d results for '%s'", len(results), query[:50])
    return results


async def _search_skillssh_npx(query: str, limit: int = 5) -> list[SearchResult]:
    """Search via `npx skills find <query>` subprocess."""

    npx_path = settings.skillssh_npx_path
    if not shutil.which(npx_path):
        logger.debug("npx not found at '%s'", npx_path)
        return []

    try:
        # Inherit host env + add non-interactive flags
        env = {**os.environ}
        env["DISABLE_TELEMETRY"] = "1"
        env["CI"] = "1"
        env["NO_COLOR"] = "1"
        env["TERM"] = "dumb"

        logger.debug("Running: %s -y skills find '%s'", npx_path, query[:50])

        process = await asyncio.create_subprocess_exec(
            npx_path,
            "-y",
            "skills",
            "find",
            query,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=settings.skillssh_search_timeout,
        )

        if process.returncode != 0:
            logger.debug(
                "npx skills find failed (rc=%d): %s",
                process.returncode,
                stderr.decode()[:200],
            )
            return []

        raw_output = stdout.decode()
        # Strip ANSI escape codes
        clean_output = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", raw_output)
        logger.debug(
            "npx skills find returned %d bytes, %d lines",
            len(clean_output),
            clean_output.count("\n"),
        )

        results = _parse_skillssh_output(clean_output, limit)
        logger.debug("Parsed %d skills from npx output", len(results))
        return results

    except asyncio.TimeoutError:
        logger.warning(
            "skills.sh search timed out after %.0fs", settings.skillssh_search_timeout
        )
        return []
    except Exception as e:
        logger.debug("skills.sh npx search error: %s", e)
        return []


def _parse_skillssh_output(output: str, limit: int = 5) -> list[SearchResult]:
    """Parse `npx skills find` stdout into SearchResult list.

    Actual output format (one result per block):
        Install with npx skills add <owner/repo@skill>
        vercel-labs/agent-skills@web-design-guidelines 117.1K installs
        └ https://skills.sh/vercel-labs/agent-skills/web-design-guidelines
    """
    results: list[SearchResult] = []

    lines = output.strip().split("\n")
    for line in lines:
        line = line.strip()

        # Match: "owner/repo@skill-name" optionally followed by " 117.1K installs"
        match = re.match(
            r"^([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)@([a-zA-Z0-9_:.-]+)(?:\s+(.+installs?))?",
            line,
        )
        if match:
            owner_repo = match.group(1)
            skill_name = match.group(2)
            installs_text = match.group(3) or ""
            description = f"Agent skill from {owner_repo}"
            if installs_text:
                description += f" ({installs_text.strip()})"

            # Parse install count for relevance scaling
            relevance = _installs_to_relevance(installs_text)

            results.append(
                SearchResult(
                    name=skill_name,
                    description=description,
                    source="skillssh",
                    url=f"https://github.com/{owner_repo}",
                    relevance=relevance,
                    tags=["agent-skill", "skills.sh"],
                )
            )

        # Also match URL lines "└ https://skills.sh/owner/repo/skill-name"
        url_match = re.match(r".*https://skills\.sh/([^/]+)/([^/]+)/(.+)$", line)
        if url_match and results:
            owner = url_match.group(1)
            repo = url_match.group(2)
            skill = url_match.group(3)
            if results[-1].name == skill:
                results[-1].url = f"https://github.com/{owner}/{repo}"

        # Fix #4: Enforce limit
        if len(results) >= limit:
            break

    return results


def _installs_to_relevance(installs_text: str) -> float:
    """Convert install count text to a relevance score 0.80-0.95.

    - 100K+ installs → 0.95
    - 10K+ → 0.93
    - 1K+ → 0.91
    - 100+ → 0.88
    - <100 or unknown → 0.85
    """
    if not installs_text:
        return 0.85

    # Parse "117.1K installs" → 117100
    text = installs_text.strip().split()[0]  # "117.1K"
    try:
        multiplier = 1.0
        if text.upper().endswith("K"):
            multiplier = 1_000
            text = text[:-1]
        elif text.upper().endswith("M"):
            multiplier = 1_000_000
            text = text[:-1]
        count = float(text) * multiplier
    except (ValueError, IndexError):
        return 0.85

    if count >= 100_000:
        return 0.95
    if count >= 10_000:
        return 0.93
    if count >= 1_000:
        return 0.91
    if count >= 100:
        return 0.88
    return 0.85


async def _search_skillssh_github(query: str, limit: int = 5) -> list[SearchResult]:
    """Fallback: search GitHub repos with agent-skill topics + SKILL.md convention.

    Uses the repositories search API (more reliable than code search).
    """
    try:
        # Fix #2: Use filename search + SKILL.md convention (OR on topics breaks)
        search_query = f"{query} filename:SKILL.md language:markdown"
        headers = {"Accept": "application/vnd.github.v3+json"}
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"

        async with httpx.AsyncClient(timeout=settings.search_timeout) as client:
            resp = await client.get(
                "https://api.github.com/search/repositories",
                params={"q": search_query, "sort": "stars", "per_page": limit * 2},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        results: list[SearchResult] = []
        seen: set[str] = set()

        for repo in data.get("items", []):
            full_name = repo.get("full_name", "")
            if full_name in seen:
                continue
            seen.add(full_name)

            repo_name = repo.get("name", full_name.split("/")[-1])

            results.append(
                SearchResult(
                    name=repo_name,
                    description=repo.get("description", "")
                    or f"Agent skill from {full_name}",
                    source="skillssh",
                    url=repo.get("html_url", f"https://github.com/{full_name}"),
                    relevance=0.85,
                    tags=repo.get("topics", []) + ["github-fallback"],
                )
            )

            if len(results) >= limit:
                break

        return results

    except Exception as e:
        logger.warning("skills.sh GitHub fallback search failed: %s", e)
        return []


def _extract_skill_name_from_path(path: str) -> str:
    """Extract skill name from SKILL.md file path.

    Examples:
        "skills/web-design-guidelines/SKILL.md" → "web-design-guidelines"
        "SKILL.md" → ""
        "skills/.curated/frontend-design/SKILL.md" → "frontend-design"
    """
    parts = path.replace("\\", "/").split("/")
    # Find SKILL.md and take the parent directory name
    for i, part in enumerate(parts):
        if part.upper() == "SKILL.MD" and i > 0:
            return parts[i - 1]
    return ""


# ─── 2. Official MCP Registry ──────────────────────────────────────────────


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

            results.append(
                SearchResult(
                    name=server.get("name", "unknown"),
                    description=server.get("description", ""),
                    source="mcp_registry",
                    url=repo_url,
                    relevance=0.85,
                    tags=[],
                )
            )

        set_cached(*cache_key, payload=[r.model_dump() for r in results])
        logger.info("MCP Registry: found %d results for '%s'", len(results), query)
        return results

    except Exception as e:
        logger.warning("MCP Registry search failed: %s", e)
        return []


# ─── 3. Smithery ───────────────────────────────────────────────────────────


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
            results.append(
                SearchResult(
                    name=server.get("qualifiedName", server.get("name", "unknown")),
                    description=server.get("description", ""),
                    source="smithery",
                    url=server.get("homepage", server.get("url", "")),
                    relevance=0.70,
                    tags=server.get("tags", []),
                )
            )

        set_cached(*cache_key, payload=[r.model_dump() for r in results])
        logger.info("Smithery: found %d results for '%s'", len(results), query)
        return results

    except Exception as e:
        logger.warning("Smithery search failed: %s", e)
        return []


# ─── 4. Glama.ai ──────────────────────────────────────────────────────────


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
                results.append(
                    SearchResult(
                        name=server.get("slug", server.get("name", "unknown")),
                        description=server.get("description", ""),
                        source="glama",
                        url=server.get("url", server.get("homepage", "")),
                        relevance=0.65,
                        tags=server.get("attributes", []),
                    )
                )

        set_cached(*cache_key, payload=[r.model_dump() for r in results])
        logger.info("Glama: found %d results for '%s'", len(results), query)
        return results

    except Exception as e:
        logger.warning("Glama search failed: %s", e)
        return []


# ─── 5. GitHub ─────────────────────────────────────────────────────────────


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
            results.append(
                SearchResult(
                    name=repo.get("full_name", repo.get("name", "unknown")),
                    description=repo.get("description", "") or "",
                    source="github",
                    url=repo.get("html_url", ""),
                    relevance=0.50,
                    tags=repo.get("topics", []),
                )
            )

        set_cached(*cache_key, payload=[r.model_dump() for r in results])
        logger.info("GitHub: found %d results for '%s'", len(results), query)
        return results

    except httpx.HTTPStatusError as e:
        logger.warning("GitHub API error %d: %s", e.response.status_code, e)
        return []
    except Exception as e:
        logger.warning("GitHub search failed: %s", e)
        return []


# ─── 6. Combined Search ───────────────────────────────────────────────────


async def search_remote(
    query: str,
    limit: int = 5,
    with_trust: bool = True,
) -> list[SearchResult]:
    """Search all remote registries in parallel, deduplicate, and optionally score trust.

    Order: Skills.sh → MCP Registry → Smithery → Glama → GitHub (by trust level).
    """
    # Query all registries in parallel
    tasks = [
        search_skillssh(query, limit),
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

    # Fix #1: Deduplicate by composite key (source_type + normalized_name)
    # Skills from different repos (e.g., vercel-labs and antfu both have
    # web-design-guidelines) are kept; same skill from same source is deduped.
    seen: set[str] = set()
    unique: list[SearchResult] = []
    for r in sorted(all_results, key=lambda x: x.relevance, reverse=True):
        norm_name = _normalize_name(r.name)
        # For skillssh: use url as disambiguator (different repos = different skills)
        if r.source == "skillssh" and r.url:
            dedup_key = f"{norm_name}:{r.url}"
        else:
            dedup_key = f"{norm_name}:{r.source}"
        if dedup_key not in seen:
            seen.add(dedup_key)
            unique.append(r)

    # Fix #6: Evaluate real trust for all GitHub-hosted results
    if with_trust:
        for r in unique:
            if r.url and "github.com" in r.url:
                # Real trust eval for any result with a GitHub URL
                trust = await evaluate_github_repo(r.url)
                r.trust = trust
                if r.source == "skillssh":
                    # Skills.sh: keep high relevance, light trust adjustment
                    r.relevance = round(r.relevance * 0.7 + trust.score * 0.3, 3)
                else:
                    # GitHub generic: heavier trust weighting
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
            name = name[len(prefix) :]
    for suffix in ("-mcp", "-server", "-skill", ".skill.md"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    # Remove owner prefix (e.g., "user/repo" -> "repo")
    if "/" in name:
        name = name.split("/")[-1]
    return name

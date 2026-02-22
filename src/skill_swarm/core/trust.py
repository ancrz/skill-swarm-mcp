"""Trust score engine — git-quality signals for evaluating remote skills.

Inspired by npms.io, OpenSSF Scorecard, and Libraries.io SourceRank.
Evaluates 5 dimensions: Recency, Popularity, Maintenance, Security, Completeness.
"""

import logging
import math
from datetime import datetime, timezone

import httpx

from skill_swarm.config import settings
from skill_swarm.core.cache import get_cached, set_cached
from skill_swarm.models import TrustScore

logger = logging.getLogger("skill-swarm.trust")

# License trust levels for skill installation
_LICENSE_TRUST: dict[str, float] = {
    "MIT": 1.0,
    "Apache-2.0": 1.0,
    "BSD-2-Clause": 1.0,
    "BSD-3-Clause": 1.0,
    "ISC": 1.0,
    "0BSD": 1.0,
    "Unlicense": 0.9,
    "MPL-2.0": 0.85,
    "LGPL-2.1": 0.7,
    "LGPL-3.0": 0.7,
    "GPL-2.0": 0.5,
    "GPL-3.0": 0.5,
    "AGPL-3.0": 0.4,
}

# Dimension weights
WEIGHTS = {
    "recency": 0.20,
    "popularity": 0.20,
    "maintenance": 0.25,
    "security": 0.25,
    "completeness": 0.10,
}


def _log_norm(value: int, midpoint: int) -> float:
    """Logarithmic normalization: 0 at value=0, 1.0 at value=midpoint."""
    if value <= 0:
        return 0.0
    return min(math.log10(1 + value) / math.log10(1 + midpoint), 1.0)


def _days_since(iso_date: str) -> int:
    """Days between now and an ISO 8601 date string."""
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        return max(0, (datetime.now(timezone.utc) - dt).days)
    except Exception:
        return 999


def score_recency(pushed_at: str, created_at: str) -> float:
    """Exponential decay: 1.0 at day 0, ~0.5 at 180 days."""
    days = _days_since(pushed_at)
    decay = math.exp(-0.00385 * days)  # half-life 180 days

    age_days = _days_since(created_at)
    age_bonus = min(age_days / 365.0, 1.0) * 0.1  # +0.1 for repos > 1 year

    return min(decay + age_bonus, 1.0)


def score_popularity(stars: int, forks: int, watchers: int) -> float:
    """Log-normalized popularity from stars, forks, watchers."""
    s_stars = _log_norm(stars, 10000)
    s_forks = _log_norm(forks, 2000)
    s_watchers = _log_norm(watchers, 500)
    return s_stars * 0.55 + s_forks * 0.30 + s_watchers * 0.15


def score_maintenance(open_issues: int, pushed_at: str, archived: bool) -> float:
    """Maintenance estimate from repo-level API data."""
    if archived:
        return 0.05

    days = _days_since(pushed_at)
    recency = math.exp(-0.0077 * days)  # half-life 90 days

    if open_issues == 0:
        issue_signal = 0.5
    elif open_issues < 20:
        issue_signal = 0.8
    elif open_issues < 100:
        issue_signal = 0.5
    else:
        issue_signal = 0.3

    return recency * 0.6 + issue_signal * 0.4


def score_security(license_spdx: str | None, archived: bool) -> float:
    """Security score from license type and archive status."""
    if license_spdx is None:
        s_license = 0.1
    else:
        s_license = _LICENSE_TRUST.get(license_spdx, 0.3)

    s_archived = 0.0 if archived else 1.0
    return s_license * 0.6 + s_archived * 0.4


def score_completeness(
    has_description: bool,
    has_homepage: bool,
    has_topics: bool,
) -> float:
    """Metadata completeness."""
    s_desc = 1.0 if has_description else 0.0
    s_home = 1.0 if has_homepage else 0.0
    s_topics = 1.0 if has_topics else 0.0
    return s_desc * 0.4 + s_home * 0.3 + s_topics * 0.3


def compute_trust(dimensions: dict[str, float]) -> TrustScore:
    """Combine dimension scores into a final trust score."""
    available = sum(1 for v in dimensions.values() if v >= 0)
    confidence = available / len(WEIGHTS)

    total = sum(dimensions.get(k, 0.0) * w for k, w in WEIGHTS.items())
    effective = total * confidence

    if effective >= 0.75:
        verdict = "TRUST"
    elif effective >= 0.50:
        verdict = "CAUTION"
    elif effective >= 0.25:
        verdict = "WARNING"
    else:
        verdict = "REJECT"

    return TrustScore(
        score=round(total, 3),
        confidence=round(confidence, 2),
        verdict=verdict,
        dimensions={k: round(v, 3) for k, v in dimensions.items()},
    )


async def evaluate_github_repo(repo_url: str) -> TrustScore:
    """Evaluate trust score for a GitHub repository.

    Uses a single API call to /repos/{owner}/{repo} — returns stars,
    forks, license, pushed_at, archived, topics, description.
    With token: 5000 req/hour. Without: 60 req/hour.
    """
    # Check cache first
    cached = get_cached("trust", repo_url, ttl=settings.cache_trust_ttl)
    if cached:
        return TrustScore.model_validate(cached)

    # Extract owner/repo from URL
    owner_repo = _parse_github_url(repo_url)
    if not owner_repo:
        return TrustScore(verdict="UNKNOWN")

    try:
        headers = {"Accept": "application/vnd.github.v3+json"}
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"

        async with httpx.AsyncClient(timeout=settings.search_timeout) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{owner_repo}",
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        dims = {
            "recency": score_recency(
                data.get("pushed_at", ""),
                data.get("created_at", ""),
            ),
            "popularity": score_popularity(
                data.get("stargazers_count", 0),
                data.get("forks_count", 0),
                data.get("subscribers_count", 0),
            ),
            "maintenance": score_maintenance(
                data.get("open_issues_count", 0),
                data.get("pushed_at", ""),
                data.get("archived", False),
            ),
            "security": score_security(
                data.get("license", {}).get("spdx_id") if data.get("license") else None,
                data.get("archived", False),
            ),
            "completeness": score_completeness(
                bool(data.get("description")),
                bool(data.get("homepage")),
                len(data.get("topics", [])) > 0,
            ),
        }

        result = compute_trust(dims)

        # Cache the result
        set_cached("trust", repo_url, payload=result.model_dump())

        logger.info(
            "Trust score for %s: %.3f (%s)", owner_repo, result.score, result.verdict
        )
        return result

    except Exception as e:
        logger.warning("Trust evaluation failed for %s: %s", repo_url, e)
        return TrustScore(verdict="UNKNOWN")


def quick_trust_from_registry(source: str, **kwargs) -> TrustScore:
    """Quick trust estimate for registry results without GitHub API call.

    Uses base trust level of the source + available metadata.
    """
    base_trust = {
        "skillssh": 0.90,
        "mcp_registry": 0.85,
        "smithery": 0.70,
        "glama": 0.65,
        "github": 0.50,
    }

    base = base_trust.get(source, 0.3)
    dims = {
        "recency": 0.7,  # assume recent if in a registry
        "popularity": base,
        "maintenance": base,
        "security": base * 0.9,
        "completeness": base * 0.8,
    }

    return compute_trust(dims)


def _parse_github_url(url: str) -> str | None:
    """Extract 'owner/repo' from a GitHub URL."""
    url = url.rstrip("/")
    if "github.com/" in url:
        parts = url.split("github.com/")[-1].split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    # Already in owner/repo format
    if "/" in url and "." not in url.split("/")[0]:
        return url
    return None

"""E2E V2 tests — Trust scoring, cache, multi-registry, usage tracking, matcher V2.

Tests the 7 improvements:
- UC7: Trust scoring (git-quality signals)
- UC8: Cache (TTL file cache, hit/miss)
- UC9: Multi-registry search (5 sources)
- UC10: Usage tracking (event recording, classification)
- UC11: Matcher V2 accuracy (BM25F, typo tolerance, exact match boost)
- UC12: Dead skill detection
"""

import asyncio
import json
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from skill_swarm.config import settings
from skill_swarm.core.cache import get_cached, set_cached, purge_all, cache_stats
from skill_swarm.core.trust import (
    evaluate_github_repo, quick_trust_from_registry, compute_trust,
    score_recency, score_popularity, score_maintenance, score_security, score_completeness,
)
from skill_swarm.core.usage import (
    record_event, get_stats, get_all_stats, get_dead_skills, remove_stats,
)
from skill_swarm.core.matcher import match_skill, match_skills_local, _BM25FIndex
from skill_swarm.core.registry import (
    search_mcp_registry, search_smithery, search_glama, search_github, search_remote,
)
from skill_swarm.models import SkillInfo, TrustScore
from skill_swarm.tools.inventory import match_skills


class Metrics:
    def __init__(self):
        self.results: list[dict] = []

    def record(self, name: str, passed: bool, duration_ms: float, details: dict = None):
        self.results.append({
            "test": name, "passed": passed,
            "duration_ms": round(duration_ms, 1),
            "details": details or {},
        })

    def summary(self) -> dict:
        total = len(self.results)
        passed = sum(1 for r in self.results if r["passed"])
        return {
            "total": total, "passed": passed, "failed": total - passed,
            "pass_rate": f"{passed/total*100:.1f}%" if total else "N/A",
        }


metrics = Metrics()


# ============================================================================
# UC7: TRUST SCORING
# ============================================================================

def test_uc7_trust_dimensions():
    """UC7: Trust dimension formulas produce correct ranges."""
    start = time.monotonic()

    # Recency: recent push = high, old push = low
    recent = score_recency("2026-02-18T10:00:00Z", "2024-01-01T00:00:00Z")
    old = score_recency("2023-01-01T00:00:00Z", "2022-01-01T00:00:00Z")
    assert 0.8 <= recent <= 1.0, f"recent={recent}"
    assert old < 0.3, f"old={old}"

    # Popularity: high stars = high, zero = 0
    popular = score_popularity(5000, 1000, 200)
    empty = score_popularity(0, 0, 0)
    assert popular > 0.5
    assert empty == 0.0

    # Maintenance: active, not archived
    active = score_maintenance(5, "2026-02-18T10:00:00Z", False)
    archived = score_maintenance(100, "2024-01-01T00:00:00Z", True)
    assert active > 0.5
    assert archived < 0.1

    # Security: MIT = high, no license = low
    mit = score_security("MIT", False)
    no_lic = score_security(None, False)
    assert mit > 0.8
    assert no_lic < 0.5

    # Completeness
    full = score_completeness(True, True, True)
    empty_meta = score_completeness(False, False, False)
    assert full == 1.0
    assert empty_meta == 0.0

    elapsed = (time.monotonic() - start) * 1000
    metrics.record("UC7: trust dimensions", True, elapsed, {
        "recent": round(recent, 3), "old": round(old, 3),
        "popular": round(popular, 3), "mit": round(mit, 3),
    })
    return True


def test_uc7b_trust_composite():
    """UC7b: Composite trust score produces correct verdicts."""
    start = time.monotonic()

    high = compute_trust({"recency": 0.9, "popularity": 0.8, "maintenance": 0.9, "security": 0.95, "completeness": 0.8})
    low = compute_trust({"recency": 0.1, "popularity": 0.05, "maintenance": 0.1, "security": 0.1, "completeness": 0.0})

    assert high.verdict == "TRUST"
    assert high.score > 0.75
    assert low.verdict in ("REJECT", "WARNING")
    assert low.score < 0.3

    elapsed = (time.monotonic() - start) * 1000
    metrics.record("UC7b: trust composite", True, elapsed, {
        "high": f"{high.score:.3f} ({high.verdict})",
        "low": f"{low.score:.3f} ({low.verdict})",
    })
    return True


def test_uc7c_trust_github_real():
    """UC7c: Real GitHub trust evaluation with token auth."""
    start = time.monotonic()

    # modelcontextprotocol/servers is a high-trust repo
    trust = asyncio.run(evaluate_github_repo("https://github.com/modelcontextprotocol/servers"))
    elapsed = (time.monotonic() - start) * 1000

    passed = trust.score > 0.3 and trust.verdict != "UNKNOWN"
    metrics.record("UC7c: real GitHub trust", passed, elapsed, {
        "score": trust.score, "verdict": trust.verdict,
        "dimensions": trust.dimensions,
    })
    return passed


# ============================================================================
# UC8: CACHE
# ============================================================================

def test_uc8_cache_set_get():
    """UC8: Cache set and get with TTL."""
    start = time.monotonic()
    purge_all()

    # Set a value
    set_cached("test", "query1", payload={"results": [1, 2, 3]})

    # Get it back (should hit)
    result = get_cached("test", "query1", ttl=3600)
    assert result == {"results": [1, 2, 3]}

    # Different key (should miss)
    miss = get_cached("test", "query2", ttl=3600)
    assert miss is None

    elapsed = (time.monotonic() - start) * 1000
    purge_all()
    metrics.record("UC8: cache set/get", True, elapsed)
    return True


def test_uc8b_cache_ttl_expiry():
    """UC8b: Expired cache entries return None."""
    start = time.monotonic()
    purge_all()

    set_cached("test", "expired", payload={"old": True})

    # TTL of 0 seconds = already expired
    result = get_cached("test", "expired", ttl=0)
    assert result is None

    elapsed = (time.monotonic() - start) * 1000
    purge_all()
    metrics.record("UC8b: cache TTL expiry", True, elapsed)
    return True


def test_uc8c_cache_search_hit():
    """UC8c: Second search should be a cache hit (faster)."""
    start = time.monotonic()

    # First call: network
    t1 = time.monotonic()
    r1 = asyncio.run(search_smithery("filesystem", limit=2))
    d1 = (time.monotonic() - t1) * 1000

    # Second call: cache
    t2 = time.monotonic()
    r2 = asyncio.run(search_smithery("filesystem", limit=2))
    d2 = (time.monotonic() - t2) * 1000

    elapsed = (time.monotonic() - start) * 1000

    # Cache hit should be significantly faster
    passed = d2 < d1 * 0.5 or d2 < 5  # either 50% faster or under 5ms
    metrics.record("UC8c: cache search hit", passed, elapsed, {
        "first_ms": round(d1, 1), "second_ms": round(d2, 1),
        "speedup": f"{d1/max(d2,0.01):.1f}x",
    })
    return passed


# ============================================================================
# UC9: MULTI-REGISTRY SEARCH
# ============================================================================

def test_uc9_multi_registry():
    """UC9: Search across multiple registries with deduplication."""
    start = time.monotonic()
    purge_all()  # clean cache for fair test

    results = asyncio.run(search_remote("filesystem", limit=10, with_trust=False))
    elapsed = (time.monotonic() - start) * 1000

    sources = set(r.source for r in results)
    names = [r.name for r in results]

    # Should have results from at least 2 different registries
    passed = len(sources) >= 2 and len(results) > 0
    metrics.record("UC9: multi-registry", passed, elapsed, {
        "total_results": len(results),
        "sources": list(sources),
        "names": names[:5],
    })
    return passed


def test_uc9b_trust_in_results():
    """UC9b: Remote search results include trust scores."""
    start = time.monotonic()

    results = asyncio.run(search_remote("mcp server", limit=5, with_trust=True))
    elapsed = (time.monotonic() - start) * 1000

    has_trust = sum(1 for r in results if r.trust is not None)

    passed = has_trust > 0
    metrics.record("UC9b: trust in results", passed, elapsed, {
        "with_trust": has_trust,
        "total": len(results),
        "sample": [
            {"name": r.name, "source": r.source, "trust": r.trust.score if r.trust else None}
            for r in results[:3]
        ],
    })
    return passed


# ============================================================================
# UC10: USAGE TRACKING
# ============================================================================

def test_uc10_usage_events():
    """UC10: Record usage events and classify primary usage."""
    start = time.monotonic()

    # Clean state
    remove_stats("test-usage-skill")

    record_event("test-usage-skill", "match")
    record_event("test-usage-skill", "match")
    record_event("test-usage-skill", "cherry_pick")

    stats = get_stats("test-usage-skill")
    assert stats.match_hits == 2
    assert stats.cherry_pick_count == 1
    assert stats.primary_usage == "cherry_pick_only"  # has cherry_pick but no full_read
    assert stats.last_usage_type == "cherry_pick"
    assert stats.last_used != ""

    # Now add a full read
    record_event("test-usage-skill", "full_read")
    stats2 = get_stats("test-usage-skill")
    assert stats2.primary_usage == "full"

    elapsed = (time.monotonic() - start) * 1000
    remove_stats("test-usage-skill")
    metrics.record("UC10: usage events", True, elapsed, {
        "classification_flow": "match_only -> cherry_pick_only -> full",
    })
    return True


def test_uc10b_dead_detection():
    """UC10b: Detect dead skills (installed but never used)."""
    start = time.monotonic()

    # Create a dead skill entry
    from skill_swarm.core.usage import mark_installed
    remove_stats("dead-skill-test")
    mark_installed("dead-skill-test")

    dead = get_dead_skills()
    passed = "dead-skill-test" in dead

    elapsed = (time.monotonic() - start) * 1000
    remove_stats("dead-skill-test")
    metrics.record("UC10b: dead skill detection", passed, elapsed)
    return passed


# ============================================================================
# UC11: MATCHER V2 ACCURACY
# ============================================================================

def test_uc11_exact_match_boost():
    """UC11: Exact match should score much higher than partial."""
    start = time.monotonic()

    skill = SkillInfo(name="pdf-parser", description="Parse PDF documents", tags=["pdf", "parser"])

    exact = match_skill(skill, "pdf-parser")
    partial = match_skill(skill, "parse pdf")
    unrelated = match_skill(skill, "kubernetes deployment")

    assert exact > partial, f"exact={exact} should > partial={partial}"
    assert partial > unrelated, f"partial={partial} should > unrelated={unrelated}"
    assert exact > 0.4, f"exact match should be > 0.4, got {exact}"

    elapsed = (time.monotonic() - start) * 1000
    metrics.record("UC11: exact match boost", True, elapsed, {
        "exact": round(exact, 3), "partial": round(partial, 3), "unrelated": round(unrelated, 3),
    })
    return True


def test_uc11b_typo_tolerance():
    """UC11b: Fuzzy matching should tolerate typos."""
    start = time.monotonic()

    skill = SkillInfo(name="pdf-parser", description="Parse PDF documents", tags=["pdf", "parser"])

    correct = match_skill(skill, "pdf parser")
    typo = match_skill(skill, "pdf parsr")  # typo: parsr

    # Typo score should still be meaningful (> 0.05) thanks to rapidfuzz
    assert typo > 0.05, f"typo score too low: {typo}"
    assert correct > typo  # correct should still be higher

    elapsed = (time.monotonic() - start) * 1000
    metrics.record("UC11b: typo tolerance", True, elapsed, {
        "correct": round(correct, 3), "typo": round(typo, 3),
        "typo_retention": f"{typo/correct*100:.0f}%",
    })
    return True


def test_uc11c_bm25f_field_weights():
    """UC11c: BM25F should weight name matches higher than description."""
    start = time.monotonic()

    skill_name_match = SkillInfo(name="docker-deploy", description="General purpose tool", tags=["tool"])
    skill_desc_match = SkillInfo(name="general-tool", description="Deploy docker containers", tags=["tool"])

    score_name = match_skill(skill_name_match, "docker deploy")
    score_desc = match_skill(skill_desc_match, "docker deploy")

    # Name match should score higher than description-only match
    assert score_name > score_desc, f"name={score_name} should > desc={score_desc}"

    elapsed = (time.monotonic() - start) * 1000
    metrics.record("UC11c: BM25F field weights", True, elapsed, {
        "name_match": round(score_name, 3), "desc_match": round(score_desc, 3),
    })
    return True


def test_uc11d_local_match_v2():
    """UC11d: match_skills_local with V2 scorer finds skill-swarm."""
    start = time.monotonic()

    results = match_skills("manage agent skills", threshold=0.1)
    elapsed = (time.monotonic() - start) * 1000

    found = any("skill-swarm" in r.get("name", "") for r in results)
    top = results[0] if results else {}

    passed = found
    metrics.record("UC11d: local match V2", passed, elapsed, {
        "found": found,
        "top_match": top.get("name", "none"),
        "top_relevance": top.get("relevance_pct", 0),
    })
    return passed


# ============================================================================
# UC12: DEAD SKILL DETECTION (integrated flow)
# ============================================================================

def test_uc12_skill_health():
    """UC12: skill_health tool surfaces dead skills and usage stats."""
    start = time.monotonic()

    from skill_swarm.core.usage import mark_installed
    remove_stats("zombie-skill")
    mark_installed("zombie-skill")

    # Import and call
    all_stats = get_all_stats()
    dead = get_dead_skills()
    stats = cache_stats()

    passed = "zombie-skill" in dead and stats["entries"] >= 0
    elapsed = (time.monotonic() - start) * 1000

    remove_stats("zombie-skill")
    metrics.record("UC12: skill health", passed, elapsed, {
        "dead_skills": dead,
        "cache_entries": stats["entries"],
        "tracked_skills": len(all_stats),
    })
    return passed


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("SKILL-SWARM E2E V2 TESTS")
    print("Testing: Trust, Cache, Multi-Registry, Usage, Matcher V2")
    print("=" * 70)

    tests = [
        ("UC7:   Trust dimension formulas", test_uc7_trust_dimensions),
        ("UC7b:  Trust composite verdicts", test_uc7b_trust_composite),
        ("UC7c:  Real GitHub trust eval", test_uc7c_trust_github_real),
        ("UC8:   Cache set/get", test_uc8_cache_set_get),
        ("UC8b:  Cache TTL expiry", test_uc8b_cache_ttl_expiry),
        ("UC8c:  Cache search hit", test_uc8c_cache_search_hit),
        ("UC9:   Multi-registry search", test_uc9_multi_registry),
        ("UC9b:  Trust in search results", test_uc9b_trust_in_results),
        ("UC10:  Usage event tracking", test_uc10_usage_events),
        ("UC10b: Dead skill detection", test_uc10b_dead_detection),
        ("UC11:  Exact match boost", test_uc11_exact_match_boost),
        ("UC11b: Typo tolerance", test_uc11b_typo_tolerance),
        ("UC11c: BM25F field weights", test_uc11c_bm25f_field_weights),
        ("UC11d: Local match V2", test_uc11d_local_match_v2),
        ("UC12:  Skill health", test_uc12_skill_health),
    ]

    for name, test_fn in tests:
        try:
            print(f"\n[TEST] {name}")
            result = test_fn()
            print(f"  -> {'PASS' if result else 'FAIL'}")
        except Exception as e:
            print(f"  -> ERROR: {e}")
            metrics.record(name, False, 0, {"error": str(e)})

    summary = metrics.summary()
    print(f"\n{'=' * 70}")
    print(f"V2 RESULTS: {summary['passed']}/{summary['total']} ({summary['pass_rate']})")
    print(f"{'=' * 70}")

    if summary["failed"] > 0:
        print("\n--- FAILURES ---")
        for r in metrics.results:
            if not r["passed"]:
                print(f"  {r['test']}: {r['details']}")

    print("\n--- DETAILED ---")
    for r in metrics.results:
        s = "+" if r["passed"] else "X"
        print(f"  {s} {r['test']:<40} {r['duration_ms']:>8.1f}ms  {r['details']}")

    # Write report
    report = Path("/tmp/skill-swarm-v2-effectiveness.json")
    report.write_text(json.dumps({"summary": summary, "results": metrics.results}, indent=2, default=str))
    print(f"\nReport: {report}")

    exit(1 if summary["failed"] > 0 else 0)

"""End-to-end effectiveness tests for skill-swarm.

Tests every use case with real operations and measures effectiveness
metrics for fine-tuning the project.

Ontological approach:
- Vertical: config → core → tools → server (dependency chain)
- Horizontal: search ↔ install ↔ inventory (peer operations)
- Co-dependencies: manifest.json, ~/.agent/skills/, symlinks
"""

import asyncio
import json
import shutil
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from skill_swarm.config import settings
from skill_swarm.core.installer import load_manifest, save_manifest, install_skill, uninstall_skill
from skill_swarm.core.scanner import scan_skill
from skill_swarm.core.registry import search_smithery, search_github
from skill_swarm.tools.cherry_pick import cherry_pick_context
from skill_swarm.tools.inventory import list_skills, match_skills, get_skill_info
from skill_swarm.tools.search import search_skills

# ============================================================================
# METRICS COLLECTOR
# ============================================================================

class Metrics:
    def __init__(self):
        self.results: list[dict] = []

    def record(self, test_name: str, passed: bool, duration_ms: float, details: dict = None):
        self.results.append({
            "test": test_name,
            "passed": passed,
            "duration_ms": round(duration_ms, 1),
            "details": details or {},
        })

    def summary(self) -> dict:
        total = len(self.results)
        passed = sum(1 for r in self.results if r["passed"])
        failed = total - passed
        avg_ms = sum(r["duration_ms"] for r in self.results) / total if total else 0
        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": f"{(passed/total*100):.1f}%" if total else "N/A",
            "avg_duration_ms": round(avg_ms, 1),
            "failures": [r for r in self.results if not r["passed"]],
        }


metrics = Metrics()


def timed(fn):
    """Decorator to measure execution time."""
    def wrapper(*args, **kwargs):
        start = time.monotonic()
        result = fn(*args, **kwargs)
        elapsed = (time.monotonic() - start) * 1000
        return result, elapsed
    return wrapper


# ============================================================================
# USE CASE 1: LOCAL SKILL MATCHING (match_skills)
# ============================================================================

def test_uc1_match_existing_skill():
    """UC1: Agent asks 'do I have a skill for managing skills?' → should find skill-swarm."""
    start = time.monotonic()
    results = match_skills("manage agent skills install", threshold=0.05)
    elapsed = (time.monotonic() - start) * 1000

    found = len(results) > 0
    top_match = results[0] if results else {}
    relevance = top_match.get("relevance_pct", 0)

    passed = found and relevance >= 5
    metrics.record("UC1: match existing skill", passed, elapsed, {
        "query": "manage agent skills install",
        "results_count": len(results),
        "top_match": top_match.get("name", "none"),
        "top_relevance_pct": relevance,
        "verdict": "FOUND" if found else "MISS",
    })
    return passed


def test_uc1b_match_no_match():
    """UC1b: Agent asks for something that doesn't exist locally → empty results."""
    start = time.monotonic()
    results = match_skills("quantum computing simulation", threshold=0.5)
    elapsed = (time.monotonic() - start) * 1000

    passed = len(results) == 0
    metrics.record("UC1b: no local match", passed, elapsed, {
        "query": "quantum computing simulation",
        "results_count": len(results),
        "verdict": "CORRECT_EMPTY" if passed else "FALSE_POSITIVE",
    })
    return passed


def test_uc1c_match_partial():
    """UC1c: Partial match — query overlaps slightly with skill-swarm tags."""
    start = time.monotonic()
    results = match_skills("cherry pick context", threshold=0.05)
    elapsed = (time.monotonic() - start) * 1000

    found = any("skill-swarm" in r.get("name", "") for r in results)
    metrics.record("UC1c: partial match (cherry-pick)", found, elapsed, {
        "query": "cherry pick context",
        "results": results,
        "verdict": "PARTIAL_MATCH" if found else "MISS",
    })
    return found


# ============================================================================
# USE CASE 2: REMOTE SEARCH (search_skills remote)
# ============================================================================

def test_uc2_search_smithery():
    """UC2: Search Smithery for MCP servers."""
    start = time.monotonic()
    results = asyncio.run(search_smithery("filesystem", limit=3))
    elapsed = (time.monotonic() - start) * 1000

    passed = len(results) > 0
    metrics.record("UC2: Smithery search", passed, elapsed, {
        "query": "filesystem",
        "results_count": len(results),
        "results": [{"name": r.name, "source": r.source} for r in results],
        "verdict": "FOUND" if passed else "API_FAIL",
    })
    return passed


def test_uc2b_search_github():
    """UC2b: Search GitHub for skill repos."""
    start = time.monotonic()
    results = asyncio.run(search_github("mcp server", limit=3))
    elapsed = (time.monotonic() - start) * 1000

    passed = len(results) > 0
    metrics.record("UC2b: GitHub search", passed, elapsed, {
        "query": "mcp server",
        "results_count": len(results),
        "results": [{"name": r.name, "source": r.source} for r in results],
        "verdict": "FOUND" if passed else "API_FAIL_OR_RATE_LIMIT",
    })
    return passed


def test_uc2c_search_combined():
    """UC2c: Combined local + remote search."""
    start = time.monotonic()
    results = asyncio.run(search_skills("skill management", scope="all", limit=5))
    elapsed = (time.monotonic() - start) * 1000

    local = [r for r in results if r.source == "local"]
    remote = [r for r in results if r.source != "local"]

    passed = len(results) > 0
    metrics.record("UC2c: combined search", passed, elapsed, {
        "query": "skill management",
        "total": len(results),
        "local_count": len(local),
        "remote_count": len(remote),
        "verdict": "COMBINED" if (local and remote) else "PARTIAL",
    })
    return passed


# ============================================================================
# USE CASE 3: INSTALL + UNINSTALL (full lifecycle)
# ============================================================================

def test_uc3_install_from_url():
    """UC3: Install a skill from a raw markdown URL."""
    # Create a temporary test skill served from local file
    test_skill_content = """---
name: test-e2e-skill
description: A test skill for end-to-end validation
version: 0.1.0
tags: [test, e2e, validation]
---

# Test E2E Skill

## Setup

Install the dependencies.

## Usage

Run the main command.

## Troubleshooting

Check logs if something fails.
"""
    # Write to a temp file and install from it
    tmp_skill = Path("/tmp/test-e2e-skill.skill.md")
    tmp_skill.write_text(test_skill_content)

    start = time.monotonic()
    result = asyncio.run(install_skill(
        name="test-e2e-skill",
        source=str(tmp_skill),
        agents=["claude", "gemini"],
    ))
    elapsed = (time.monotonic() - start) * 1000

    # Verify installation (subdirectory structure)
    skill_exists = settings.skill_path("test-e2e-skill").exists()
    claude_link = (settings.agent_dirs["claude"] / "test-e2e-skill").is_symlink()
    gemini_link = (settings.agent_dirs["gemini"] / "test-e2e-skill").is_symlink()
    in_manifest = "test-e2e-skill" in load_manifest().skills

    passed = result.success and skill_exists and claude_link and gemini_link and in_manifest
    metrics.record("UC3: install from file", passed, elapsed, {
        "success": result.success,
        "skill_exists": skill_exists,
        "claude_symlink": claude_link,
        "gemini_symlink": gemini_link,
        "in_manifest": in_manifest,
        "security_score": result.security_score,
        "errors": result.errors,
        "verdict": "INSTALLED" if passed else "INSTALL_FAILED",
    })

    tmp_skill.unlink(missing_ok=True)
    return passed


def test_uc3b_install_duplicate():
    """UC3b: Installing the same skill again should detect it's already installed."""
    tmp_skill = Path("/tmp/test-e2e-skill-dup.skill.md")
    tmp_skill.write_text("---\nname: test-e2e-skill\n---\n# Dup\n")

    start = time.monotonic()
    result = asyncio.run(install_skill(
        name="test-e2e-skill",
        source=str(tmp_skill),
    ))
    elapsed = (time.monotonic() - start) * 1000

    passed = result.success and any("Already" in e for e in result.errors)
    metrics.record("UC3b: duplicate install detected", passed, elapsed, {
        "success": result.success,
        "errors": result.errors,
        "verdict": "DEDUP_OK" if passed else "DEDUP_FAIL",
    })

    tmp_skill.unlink(missing_ok=True)
    return passed


def test_uc3c_install_malicious_blocked():
    """UC3c: Installing a skill with malicious code should be BLOCKED."""
    # Pre-clean any stale artifacts from previous runs
    stale_dir = settings.skill_dir("evil-skill")
    if stale_dir.exists():
        shutil.rmtree(stale_dir, ignore_errors=True)
    for agent_dir in settings.agent_dirs.values():
        link = agent_dir / "evil-skill"
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            shutil.rmtree(link, ignore_errors=True)
    manifest = load_manifest()
    manifest.skills.pop("evil-skill", None)
    save_manifest(manifest)

    Path("/tmp/evil-skill.py")
    tmp_dir = Path("/tmp/evil-skill-src")
    tmp_dir.mkdir(exist_ok=True)

    (tmp_dir / "evil.py").write_text(
        'import os\nos.system(input("cmd: "))\n'
        'eval(input("code: "))\n'
    )
    (tmp_dir / "SKILL.md").write_text("---\nname: evil-skill\n---\n# Evil\n")

    # Create a zip of the evil skill
    import zipfile
    zip_path = Path("/tmp/evil-skill.zip")
    with zipfile.ZipFile(zip_path, 'w') as zf:
        for f in tmp_dir.rglob("*"):
            zf.write(f, f.relative_to(tmp_dir))

    start = time.monotonic()
    result = asyncio.run(install_skill(
        name="evil-skill",
        source=str(zip_path),
    ))
    elapsed = (time.monotonic() - start) * 1000

    skill_not_installed = not settings.skill_path("evil-skill").exists()

    passed = not result.success and skill_not_installed
    metrics.record("UC3c: malicious skill blocked", passed, elapsed, {
        "success": result.success,
        "blocked": skill_not_installed,
        "errors": result.errors,
        "security_score": result.security_score,
        "verdict": "BLOCKED_OK" if passed else "SECURITY_BYPASS",
    })

    # Cleanup
    shutil.rmtree(tmp_dir, ignore_errors=True)
    zip_path.unlink(missing_ok=True)
    return passed


def test_uc3d_uninstall():
    """UC3d: Uninstall should remove skill + all symlinks + manifest entry."""
    start = time.monotonic()
    result = asyncio.run(uninstall_skill("test-e2e-skill"))
    elapsed = (time.monotonic() - start) * 1000

    skill_gone = not settings.skill_dir("test-e2e-skill").exists()
    claude_gone = not (settings.agent_dirs["claude"] / "test-e2e-skill").exists()
    gemini_gone = not (settings.agent_dirs["gemini"] / "test-e2e-skill").exists()
    manifest_clean = "test-e2e-skill" not in load_manifest().skills

    passed = result.success and skill_gone and claude_gone and gemini_gone and manifest_clean
    metrics.record("UC3d: uninstall complete", passed, elapsed, {
        "success": result.success,
        "skill_removed": skill_gone,
        "claude_unlinked": claude_gone,
        "gemini_unlinked": gemini_gone,
        "manifest_clean": manifest_clean,
        "verdict": "UNINSTALLED" if passed else "UNINSTALL_INCOMPLETE",
    })
    return passed


# ============================================================================
# USE CASE 4: CHERRY-PICK CONTEXT
# ============================================================================

def test_uc4_cherry_pick_exact():
    """UC4: Cherry-pick exact section from skill-swarm."""
    start = time.monotonic()
    result = cherry_pick_context("skill-swarm", ["Decision Protocol"])
    elapsed = (time.monotonic() - start) * 1000

    extracted = result.get("sections_extracted", 0)
    has_content = "Decision Protocol" in result.get("content", {})

    passed = extracted == 1 and has_content
    metrics.record("UC4: cherry-pick exact section", passed, elapsed, {
        "sections_extracted": extracted,
        "has_content": has_content,
        "available": result.get("available_sections", []),
        "verdict": "EXTRACTED" if passed else "MISS",
    })
    return passed


def test_uc4b_cherry_pick_multiple():
    """UC4b: Cherry-pick multiple sections at once."""
    start = time.monotonic()
    result = cherry_pick_context("skill-swarm", ["Architecture", "Anti-Patterns", "Security"])
    elapsed = (time.monotonic() - start) * 1000

    extracted = result.get("sections_extracted", 0)

    passed = extracted >= 2
    metrics.record("UC4b: cherry-pick multiple sections", passed, elapsed, {
        "sections_extracted": extracted,
        "content_keys": list(result.get("content", {}).keys()),
        "not_found": result.get("not_found", []),
        "verdict": f"EXTRACTED_{extracted}" if passed else "PARTIAL_FAIL",
    })
    return passed


def test_uc4c_cherry_pick_fuzzy():
    """UC4c: Cherry-pick with fuzzy section name (partial match)."""
    start = time.monotonic()
    result = cherry_pick_context("skill-swarm", ["MCP Tools"])
    elapsed = (time.monotonic() - start) * 1000

    extracted = result.get("sections_extracted", 0)

    passed = extracted >= 1
    metrics.record("UC4c: cherry-pick fuzzy match", passed, elapsed, {
        "sections_extracted": extracted,
        "matched_as": list(result.get("content", {}).keys()),
        "verdict": "FUZZY_OK" if passed else "FUZZY_FAIL",
    })
    return passed


def test_uc4d_cherry_pick_nonexistent():
    """UC4d: Cherry-pick a section that doesn't exist."""
    start = time.monotonic()
    result = cherry_pick_context("skill-swarm", ["Quantum Entanglement Protocol"])
    elapsed = (time.monotonic() - start) * 1000

    extracted = result.get("sections_extracted", 0)
    not_found = result.get("not_found", [])

    passed = extracted == 0 and len(not_found) > 0
    metrics.record("UC4d: cherry-pick nonexistent section", passed, elapsed, {
        "sections_extracted": extracted,
        "not_found": not_found,
        "verdict": "CORRECT_EMPTY" if passed else "FALSE_POSITIVE",
    })
    return passed


# ============================================================================
# USE CASE 5: INVENTORY (list_skills, get_skill_info)
# ============================================================================

def test_uc5_list_skills():
    """UC5: List all installed skills with health status."""
    start = time.monotonic()
    result = list_skills(agent="all")
    elapsed = (time.monotonic() - start) * 1000

    total = result.get("total", 0)
    has_skill_swarm = any(s["name"] == "skill-swarm" for s in result.get("skills", []))
    skills_dir_correct = ".agent/skills" in result.get("skills_dir", "")

    passed = total >= 1 and has_skill_swarm and skills_dir_correct
    metrics.record("UC5: list skills", passed, elapsed, {
        "total": total,
        "has_skill_swarm": has_skill_swarm,
        "skills_dir": result.get("skills_dir", ""),
        "verdict": "INVENTORY_OK" if passed else "INVENTORY_FAIL",
    })
    return passed


def test_uc5b_list_by_agent():
    """UC5b: List skills filtered by agent."""
    start = time.monotonic()
    result_claude = list_skills(agent="claude")
    result_gemini = list_skills(agent="gemini")
    elapsed = (time.monotonic() - start) * 1000

    claude_ok = any(
        s.get("symlinks", {}).get("claude") == "ok"
        for s in result_claude.get("skills", [])
    )
    gemini_ok = any(
        s.get("symlinks", {}).get("gemini") == "ok"
        for s in result_gemini.get("skills", [])
    )

    passed = claude_ok and gemini_ok
    metrics.record("UC5b: list by agent filter", passed, elapsed, {
        "claude_linked": claude_ok,
        "gemini_linked": gemini_ok,
        "verdict": "BOTH_LINKED" if passed else "LINK_ISSUE",
    })
    return passed


def test_uc5c_get_skill_info():
    """UC5c: Get full info of skill-swarm."""
    start = time.monotonic()
    result = get_skill_info("skill-swarm")
    elapsed = (time.monotonic() - start) * 1000

    has_content = len(result.get("content", "")) > 100
    has_symlinks = "claude" in result.get("symlinks", {})
    has_path = ".agent/skills" in result.get("path", "")

    passed = has_content and has_symlinks and has_path
    metrics.record("UC5c: get skill info", passed, elapsed, {
        "content_length": len(result.get("content", "")),
        "has_symlinks": has_symlinks,
        "path": result.get("path", ""),
        "verdict": "INFO_OK" if passed else "INFO_INCOMPLETE",
    })
    return passed


def test_uc5d_get_nonexistent_skill():
    """UC5d: Get info of a skill that doesn't exist."""
    start = time.monotonic()
    result = get_skill_info("nonexistent-skill-xyz")
    elapsed = (time.monotonic() - start) * 1000

    passed = "error" in result
    metrics.record("UC5d: get nonexistent skill", passed, elapsed, {
        "error": result.get("error", ""),
        "verdict": "CORRECT_ERROR" if passed else "NO_ERROR_RETURNED",
    })
    return passed


# ============================================================================
# USE CASE 6: SECURITY SCANNER EFFECTIVENESS
# ============================================================================

def test_uc6_scanner_patterns():
    """UC6: Test all critical security patterns individually."""
    import tempfile

    patterns_to_test = [
        ("cmd_injection", 'os.system(input("cmd: "))', True),
        ("shell_inject", 'subprocess.run(["ls"], shell=True)', True),
        ("eval_inject", 'eval(input("code"))', True),
        ("exec_inject", 'exec(input("code"))', True),
        ("root_delete", 'shutil.rmtree("/")', True),
        ("cred_harvest", 'os.environ.get("API_KEY")', True),
        ("clean_code", 'print("hello world")', False),
        ("safe_subprocess", 'subprocess.run(["ls", "-la"])', False),
    ]

    results = []
    all_passed = True

    for name, code, should_flag in patterns_to_test:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "test.py").write_text(code)
            scan = scan_skill(Path(tmp), name)
            flagged = len(scan.findings) > 0
            correct = flagged == should_flag
            results.append({
                "pattern": name,
                "flagged": flagged,
                "should_flag": should_flag,
                "correct": correct,
            })
            if not correct:
                all_passed = False

    start = time.monotonic()
    (time.monotonic() - start) * 1000

    correct_count = sum(1 for r in results if r["correct"])
    total = len(results)

    metrics.record("UC6: scanner pattern accuracy", all_passed, 0, {
        "correct": correct_count,
        "total": total,
        "accuracy_pct": round(correct_count / total * 100, 1),
        "details": results,
        "verdict": f"ACCURACY_{correct_count}/{total}",
    })
    return all_passed


# ============================================================================
# MAIN: RUN ALL TESTS AND GENERATE EFFECTIVENESS REPORT
# ============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("SKILL-SWARM E2E EFFECTIVENESS TESTS")
    print("Ontological approach: testing every dependency layer")
    print("=" * 70)

    tests = [
        # UC1: Local matching
        ("UC1:  Match existing skill (local)", test_uc1_match_existing_skill),
        ("UC1b: No match (correct empty)", test_uc1b_match_no_match),
        ("UC1c: Partial match (cherry-pick tag)", test_uc1c_match_partial),
        # UC2: Remote search
        ("UC2:  Smithery registry search", test_uc2_search_smithery),
        ("UC2b: GitHub search", test_uc2b_search_github),
        ("UC2c: Combined local+remote search", test_uc2c_search_combined),
        # UC3: Install lifecycle
        ("UC3:  Install from file", test_uc3_install_from_url),
        ("UC3b: Duplicate install detection", test_uc3b_install_duplicate),
        ("UC3c: Malicious skill blocked", test_uc3c_install_malicious_blocked),
        ("UC3d: Uninstall complete", test_uc3d_uninstall),
        # UC4: Cherry-pick
        ("UC4:  Cherry-pick exact section", test_uc4_cherry_pick_exact),
        ("UC4b: Cherry-pick multiple sections", test_uc4b_cherry_pick_multiple),
        ("UC4c: Cherry-pick fuzzy match", test_uc4c_cherry_pick_fuzzy),
        ("UC4d: Cherry-pick nonexistent section", test_uc4d_cherry_pick_nonexistent),
        # UC5: Inventory
        ("UC5:  List all skills", test_uc5_list_skills),
        ("UC5b: List by agent filter", test_uc5b_list_by_agent),
        ("UC5c: Get skill info", test_uc5c_get_skill_info),
        ("UC5d: Get nonexistent skill info", test_uc5d_get_nonexistent_skill),
        # UC6: Security
        ("UC6:  Scanner pattern accuracy", test_uc6_scanner_patterns),
    ]

    for name, test_fn in tests:
        try:
            print(f"\n[TEST] {name}")
            result = test_fn()
            status = "PASS" if result else "FAIL"
            print(f"  → {status}")
        except Exception as e:
            print(f"  → ERROR: {e}")
            metrics.record(name, False, 0, {"error": str(e)})

    # ========================================================================
    # EFFECTIVENESS REPORT
    # ========================================================================
    summary = metrics.summary()

    print(f"\n{'=' * 70}")
    print("EFFECTIVENESS REPORT")
    print(f"{'=' * 70}")
    print(f"Total tests:     {summary['total']}")
    print(f"Passed:          {summary['passed']}")
    print(f"Failed:          {summary['failed']}")
    print(f"Pass rate:       {summary['pass_rate']}")
    print(f"Avg duration:    {summary['avg_duration_ms']} ms")

    if summary['failures']:
        print("\n--- FAILURES ---")
        for f in summary['failures']:
            print(f"  {f['test']}: {f['details'].get('verdict', 'UNKNOWN')}")
            if 'errors' in f['details']:
                print(f"    errors: {f['details']['errors']}")

    print("\n--- DETAILED METRICS ---")
    for r in metrics.results:
        status = "✓" if r['passed'] else "✗"
        verdict = r['details'].get('verdict', '')
        print(f"  {status} {r['test']:<45} {r['duration_ms']:>8.1f}ms  {verdict}")

    print("\n--- FINE-TUNING RECOMMENDATIONS ---")

    # Analyze effectiveness
    search_tests = [r for r in metrics.results if "search" in r['test'].lower() or "UC2" in r['test']]
    match_tests = [r for r in metrics.results if "match" in r['test'].lower() or "UC1" in r['test']]
    install_tests = [r for r in metrics.results if "install" in r['test'].lower() or "UC3" in r['test']]
    cherry_tests = [r for r in metrics.results if "cherry" in r['test'].lower() or "UC4" in r['test']]
    scanner_tests = [r for r in metrics.results if "scanner" in r['test'].lower() or "UC6" in r['test']]

    categories = [
        ("Local Matching", match_tests),
        ("Remote Search", search_tests),
        ("Install Lifecycle", install_tests),
        ("Cherry-Pick", cherry_tests),
        ("Security Scanner", scanner_tests),
    ]

    for cat_name, cat_tests in categories:
        if not cat_tests:
            continue
        cat_passed = sum(1 for t in cat_tests if t['passed'])
        cat_total = len(cat_tests)
        cat_rate = cat_passed / cat_total * 100
        avg_ms = sum(t['duration_ms'] for t in cat_tests) / cat_total

        status = "OK" if cat_rate == 100 else "NEEDS WORK" if cat_rate >= 50 else "CRITICAL"
        print(f"  {cat_name:<25} {cat_passed}/{cat_total} ({cat_rate:.0f}%)  avg={avg_ms:.0f}ms  [{status}]")

    print(f"\n{'=' * 70}")

    # Write full report to file
    report_path = Path("/tmp/skill-swarm-effectiveness.json")
    report_path.write_text(json.dumps({
        "summary": summary,
        "detailed_results": metrics.results,
    }, indent=2, default=str))
    print(f"Full report: {report_path}")

    exit(1 if summary['failed'] > 0 else 0)

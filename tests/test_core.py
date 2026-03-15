"""Core tests for skill-swarm: scanner, matcher, installer, cherry-pick."""

import json
import tempfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from skill_swarm.core.scanner import scan_skill
from skill_swarm.core.matcher import match_skill, _parse_skill_file
from skill_swarm.core.installer import _create_symlinks, normalize_skill_filenames
from skill_swarm.tools.cherry_pick import cherry_pick_context, _parse_sections
from skill_swarm.models import SkillInfo, SkillManifest
from skill_swarm.config import settings


def test_scanner_clean_skill():
    """Clean skill should pass scan."""
    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = Path(tmp)
        (skill_dir / "main.py").write_text('print("hello world")\n')
        result = scan_skill(skill_dir, "test-clean")
        assert result.passed is True
        assert result.score >= 0.5
        assert len(result.findings) == 0
        print(f"  PASS: clean skill score={result.score}")


def test_scanner_malicious_skill():
    """Skill with dangerous patterns should be blocked."""
    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = Path(tmp)
        (skill_dir / "evil.py").write_text(
            'import os\nos.system(input("cmd: "))\n'
            'eval(input("code: "))\n'
            'import shutil\nshutil.rmtree("/")\n'
        )
        result = scan_skill(skill_dir, "test-malicious")
        assert result.passed is False
        assert len(result.findings) > 0
        print(f"  PASS: malicious skill blocked, findings={len(result.findings)}")


def test_matcher_scoring():
    """Matcher should score relevant skills higher."""
    skill = SkillInfo(
        name="pdf-parser",
        description="Parse PDF files and extract text, tables, and metadata",
        tags=["pdf", "parser", "document", "text-extraction"],
    )

    score_high = match_skill(skill, "parse PDF extract text")
    score_low = match_skill(skill, "deploy kubernetes cluster")

    assert score_high > score_low
    assert score_high > 0.15  # V2 multi-signal is stricter; 0.15 = meaningful match
    print(f"  PASS: relevant={score_high:.3f}, irrelevant={score_low:.3f}")


def test_parse_skill_file():
    """Should parse YAML frontmatter from skill files."""
    with tempfile.TemporaryDirectory() as tmp:
        skill_file = Path(tmp) / "test.skill.md"
        skill_file.write_text(
            "---\n"
            "name: test-skill\n"
            "description: A test skill for parsing\n"
            "version: 1.0.0\n"
            "tags: [test, parser, demo]\n"
            "---\n\n"
            "# Test Skill\n\nContent here.\n"
        )
        info = _parse_skill_file(skill_file)
        assert info is not None
        assert info.name == "test-skill"
        assert info.description == "A test skill for parsing"
        assert "test" in info.tags
        print(f"  PASS: parsed name={info.name}, tags={info.tags}")


def test_cherry_pick_sections():
    """Should parse markdown sections correctly."""
    content = (
        "---\nname: test\n---\n\n"
        "# Main Title\n\n"
        "## Overview\n\nThis is the overview.\n\n"
        "## Installation\n\nStep 1: install\nStep 2: configure\n\n"
        "### Advanced Config\n\nSome advanced stuff.\n\n"
        "## Troubleshooting\n\nFix things here.\n"
    )
    sections = _parse_sections(content)
    assert "Overview" in sections
    assert "Installation" in sections
    assert "Advanced Config" in sections
    assert "Troubleshooting" in sections
    assert "overview" in sections["Overview"].lower()
    print(f"  PASS: parsed {len(sections)} sections: {list(sections.keys())}")


def test_cherry_pick_with_real_file():
    """Cherry-pick should extract sections from a real skill file."""
    # Use the skill-swarm's own SKILL.md
    skill_md = settings.skill_path("skill-swarm")
    if skill_md.exists():
        result = cherry_pick_context("skill-swarm", ["Decision Protocol", "Anti-Patterns"])
        assert result.get("sections_extracted", 0) > 0
        print(f"  PASS: cherry-picked {result['sections_extracted']} sections from skill-swarm")
    else:
        print(f"  SKIP: skill-swarm not yet installed to {skill_md}")


def test_manifest_roundtrip():
    """Manifest should serialize and deserialize correctly."""
    manifest = SkillManifest(
        skills={
            "test": SkillInfo(
                name="test",
                description="A test skill",
                tags=["test"],
                source="https://example.com",
                agents=["claude", "gemini"],
            )
        }
    )
    data = json.loads(manifest.model_dump_json())
    restored = SkillManifest.model_validate(data)
    assert restored.skills["test"].name == "test"
    assert restored.skills["test"].tags == ["test"]
    print("  PASS: manifest roundtrip OK")


def test_create_symlinks():
    """Symlinks should be created in agent directories (directory-level)."""
    with tempfile.TemporaryDirectory() as tmp:
        # Simulate subdirectory structure: {name}/SKILL.md
        skill_dir = Path(tmp) / "test-skill"
        skill_dir.mkdir()
        source = skill_dir / "SKILL.md"
        source.write_text("# Test\n")

        agent_dir = Path(tmp) / "agent_skills"
        agent_dir.mkdir()

        # Temporarily override settings
        original = settings.agent_dirs.copy()
        settings.agent_dirs["test-agent"] = agent_dir

        try:
            linked = _create_symlinks("SKILL.md", source, ["test-agent"])
            assert "test-agent" in linked
            link = agent_dir / "test-skill"
            assert link.is_symlink()
            assert (link / "SKILL.md").exists()
            print("  PASS: symlink created and resolves correctly")
        finally:
            settings.agent_dirs = original


def test_normalize_skill_filenames():
    """Migration should rename skill.md → SKILL.md and update manifest."""
    from skill_swarm.core.installer import load_manifest, save_manifest

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Create two skill subdirs: one lowercase (needs migration), one uppercase (already correct)
        lowercase_dir = tmp_path / "my-skill"
        lowercase_dir.mkdir()
        (lowercase_dir / "skill.md").write_text("# My Skill\n")

        uppercase_dir = tmp_path / "other-skill"
        uppercase_dir.mkdir()
        (uppercase_dir / "SKILL.md").write_text("# Other Skill\n")

        # Create a manifest referencing the lowercase path
        manifest = SkillManifest(
            skills={
                "my-skill": SkillInfo(
                    name="my-skill",
                    description="A skill with lowercase filename",
                    source="https://example.com",
                    installed_path=str(lowercase_dir / "skill.md"),
                ),
                "other-skill": SkillInfo(
                    name="other-skill",
                    description="A skill with uppercase filename",
                    source="https://example.com",
                    installed_path=str(uppercase_dir / "SKILL.md"),
                ),
            }
        )

        # Temporarily override settings.skills_dir so load/save_manifest use tmp
        original_skills_dir = settings.skills_dir
        settings.skills_dir = tmp_path

        try:
            # Write manifest to the temp dir
            save_manifest(manifest)

            # First run: should rename 1 file
            count = normalize_skill_filenames()
            assert count == 1, f"Expected 1 rename, got {count}"
            assert not (lowercase_dir / "skill.md").exists(), "lowercase skill.md should be gone"
            assert (lowercase_dir / "SKILL.md").exists(), "uppercase SKILL.md should exist"
            assert (uppercase_dir / "SKILL.md").exists(), "other-skill should be untouched"

            # Verify manifest was updated
            updated_manifest = load_manifest()
            assert updated_manifest.skills["my-skill"].installed_path == str(
                lowercase_dir / "SKILL.md"
            ), "manifest installed_path should be updated"

            # Second run: idempotent — 0 renames
            count2 = normalize_skill_filenames()
            assert count2 == 0, f"Expected 0 renames on second call, got {count2}"

            print("  PASS: migration renamed 1 file, updated manifest, idempotent on re-run")
        finally:
            settings.skills_dir = original_skills_dir


def test_normalize_query_sentence():
    """Natural language sentence should be reduced to keywords."""
    from skill_swarm.core.normalizer import normalize_query
    result = normalize_query("I need a tool that can process PDF documents and extract tables")
    assert "pdf" in result
    assert "tables" in result or "extract" in result or "documents" in result
    # Stopwords removed
    for stopword in ["i", "need", "a", "that", "can", "and"]:
        assert stopword not in result.split(), f"stopword '{stopword}' should be removed"
    print(f"  PASS: normalized to '{result}'")


def test_normalize_query_idempotent():
    """Already-clean keywords should pass through unchanged."""
    from skill_swarm.core.normalizer import normalize_query
    assert normalize_query("docker") == "docker"
    assert normalize_query("pdf parsing") == "pdf parsing"
    assert normalize_query("kubernetes helm") == "kubernetes helm"
    print("  PASS: idempotent for clean keywords")


def test_normalize_query_all_stopwords():
    """If all words are stopwords, return original query lowercased."""
    from skill_swarm.core.normalizer import normalize_query
    result = normalize_query("I want to use")
    assert len(result) > 0
    print(f"  PASS: all-stopwords returns '{result}'")


def test_normalize_query_empty():
    """Empty or whitespace-only input returns empty string."""
    from skill_swarm.core.normalizer import normalize_query
    assert normalize_query("") == ""
    assert normalize_query("   ") == ""
    print("  PASS: empty input handled")


def test_normalize_query_mixed_case_whitespace():
    """Mixed case and extra whitespace should be normalized."""
    from skill_swarm.core.normalizer import normalize_query
    result = normalize_query("  I Need To  PARSE  pdf  ")
    assert "parse" in result
    assert "pdf" in result
    assert "  " not in result  # no double spaces
    print(f"  PASS: mixed case/whitespace normalized to '{result}'")


if __name__ == "__main__":
    print("=" * 60)
    print("skill-swarm core tests")
    print("=" * 60)

    tests = [
        ("Scanner: clean skill", test_scanner_clean_skill),
        ("Scanner: malicious skill", test_scanner_malicious_skill),
        ("Matcher: relevance scoring", test_matcher_scoring),
        ("Parser: skill file frontmatter", test_parse_skill_file),
        ("Cherry-pick: section parsing", test_cherry_pick_sections),
        ("Cherry-pick: real file", test_cherry_pick_with_real_file),
        ("Manifest: roundtrip", test_manifest_roundtrip),
        ("Installer: symlinks", test_create_symlinks),
        ("Installer: normalize filenames", test_normalize_skill_filenames),
        ("Normalizer: sentence to keywords", test_normalize_query_sentence),
        ("Normalizer: idempotent", test_normalize_query_idempotent),
        ("Normalizer: all stopwords", test_normalize_query_all_stopwords),
        ("Normalizer: empty input", test_normalize_query_empty),
        ("Normalizer: mixed case/whitespace", test_normalize_query_mixed_case_whitespace),
    ]

    passed = 0
    failed = 0

    for name, test_fn in tests:
        try:
            print(f"\n[TEST] {name}")
            test_fn()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {e}")
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    print(f"{'=' * 60}")

    exit(1 if failed > 0 else 0)

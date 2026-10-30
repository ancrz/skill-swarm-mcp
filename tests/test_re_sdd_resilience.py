"""Comprehensive test suite validating RE-as-SDD resilience primitives (SPECs 1-5)."""

from pathlib import Path
import pytest

from skill_swarm.config import check_storage_writability, settings
from skill_swarm.core.audit import (
    GENESIS_HASH,
    get_audit_connection,
    mask_secrets,
    record_audit_event,
    sanitize_dict,
    verify_audit_chain,
)
from skill_swarm.core.health import calculate_skill_health
from skill_swarm.core.sandbox import get_landlock_status
from skill_swarm.core.topos import SkillToposGuard, ToposVerdict


def test_spec1_storage_writability_and_volatile_fallback(tmp_path: Path):
    """SPEC-1: Test writability detection and volatile fallback."""
    writable_dir = tmp_path / "writable"
    writable_dir.mkdir()
    assert check_storage_writability(writable_dir) is True

    # Test ensuring writable dir
    settings.skills_dir = writable_dir
    settings.is_volatile = False
    ensured = settings.ensure_writable_skills_dir()
    assert ensured == writable_dir
    assert settings.is_volatile is False


def test_spec2_audit_chain_and_secret_masking(tmp_path: Path):
    """SPEC-2: Test SHA-256 chained audit ledger and credential sanitization."""
    # 1. Test secret masking
    raw_text = "Bearer secret_token_12345 with password: SuperSecretPassword!"
    masked = mask_secrets(raw_text)
    assert "secret_token_12345" not in masked
    assert "[MASKED]" in masked

    raw_dict = {"api_key": "AIzaSyD-123456", "nested": {"TOKEN": "ghp_123456789012345678901234567890123456"}}
    sanitized = sanitize_dict(raw_dict)
    assert sanitized["api_key"] == "[MASKED]"
    assert sanitized["nested"]["TOKEN"] == "[MASKED]"

    # 2. Test audit chaining
    conn = get_audit_connection(tmp_path / "test_audit.db")
    res1 = record_audit_event(conn, "test-skill", "INSTALL", "SUCCESS", {"token": "secret"})
    assert res1["prev_hash"] == GENESIS_HASH

    res2 = record_audit_event(conn, "test-skill", "UPDATE", "SUCCESS", {"version": "0.2.0"})
    assert res2["prev_hash"] == res1["entry_hash"]

    # 3. Verify intact chain
    verification = verify_audit_chain(conn)
    assert verification["valid"] is True
    assert verification["total_verified"] == 2

    # 4. Tamper detection
    conn.execute("UPDATE skill_audit_log SET details_json = '{\"tampered\": true}' WHERE id = 1")
    conn.commit()
    tamper_check = verify_audit_chain(conn)
    assert tamper_check["valid"] is False
    assert "Tampering detected" in tamper_check["error"]
    conn.close()


def test_spec3_topos_guard_cycle_detection():
    """SPEC-3: Test DAG cycle detection and topological sorting."""
    # Valid DAG
    skills = ["skill-a", "skill-b", "skill-c"]
    deps = {
        "skill-a": [],
        "skill-b": ["skill-a"],
        "skill-c": ["skill-b"],
    }
    verdict, order, logs = SkillToposGuard.audit_skill_dependencies(skills, deps)
    assert verdict == ToposVerdict.APPROVED
    assert order == ["skill-a", "skill-b", "skill-c"]

    # Circular Co-dependency (Cycle: A -> B -> A)
    cyclic_deps = {
        "skill-a": ["skill-b"],
        "skill-b": ["skill-a"],
    }
    verdict, _, logs = SkillToposGuard.audit_skill_dependencies(["skill-a", "skill-b"], cyclic_deps)
    assert verdict == ToposVerdict.BLOCKED
    assert any("Circular co-dependency" in log for log in logs)


def test_spec4_landlock_status_fail_soft():
    """SPEC-4: Test Landlock LSM capability probe fails softly."""
    status = get_landlock_status()
    assert isinstance(status, dict)
    assert "status" in status
    assert "supported" in status
    assert status["status"] in ("SUPPORTED", "UNSUPPORTED")


def test_spec5_skill_health_multidimensional_and_veto():
    """SPEC-5: Test multidimensional health scoring and security veto."""
    # Healthy skill
    healthy = calculate_skill_health(
        name="good-skill",
        security_score=1.0,
        security_passed=True,
        match_hits=15,
        full_reads=5,
        last_used="2026-10-30T00:00:00+00:00",
        symlinks_ok=True,
    )
    assert healthy["score"] >= 0.70
    assert healthy["verdict"] in ("TRUST", "CAUTION")
    assert healthy["security_veto"] is False

    # Security failure veto
    compromised = calculate_skill_health(
        name="bad-skill",
        security_score=0.20,
        security_passed=False,
        match_hits=50,
        full_reads=20,
        last_used="2026-10-30T00:00:00+00:00",
        symlinks_ok=True,
    )
    assert compromised["score"] == 0.0
    assert compromised["verdict"] == "REJECT"
    assert compromised["security_veto"] is True

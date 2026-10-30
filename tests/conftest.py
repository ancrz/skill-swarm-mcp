"""Shared pytest configuration and fixtures for skill-swarm tests."""

import os
from pathlib import Path
import pytest

from skill_swarm.config import settings


@pytest.fixture(autouse=True)
def isolated_skills_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate skill-swarm storage into a temporary directory for test runs.

    Eliminates read-only filesystem errors by pointing settings.skills_dir and
    client symlink directories into isolated tmp_path folders.
    """
    test_skills_dir = tmp_path / "skills"
    test_skills_dir.mkdir(parents=True, exist_ok=True)

    test_claude = tmp_path / "claude_skills"
    test_agy_cfg = tmp_path / "agy_config_skills"
    test_agy_cli = tmp_path / "agy_cli_skills"
    for d in (test_claude, test_agy_cfg, test_agy_cli):
        d.mkdir(parents=True, exist_ok=True)

    test_agent_dirs = {
        "claude": [test_claude],
        "agy": [test_agy_cfg, test_agy_cli],
    }

    monkeypatch.setattr(settings, "skills_dir", test_skills_dir)
    monkeypatch.setattr(settings, "legacy_skills_dir", tmp_path / "legacy_skills")
    monkeypatch.setattr(settings, "agent_dirs", test_agent_dirs)
    monkeypatch.setenv("SKILL_SWARM_SKILLS_DIR", str(test_skills_dir))

    yield test_skills_dir

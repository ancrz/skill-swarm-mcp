"""Configuration for skill-swarm MCP server."""

import os
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Skill-swarm configuration loaded from environment and .env file."""

    # Agent Skills standard user scope. Codex discovers this directory
    # directly, so it is also the global source of truth.
    skills_dir: Path = Path.home() / ".agents" / "skills"
    legacy_skills_dir: Path = Path.home() / ".agent" / "skills"

    # Client-specific discovery directories. Each installed skill directory is
    # symlinked from these paths back to skills_dir. Codex is not listed here:
    # it consumes skills_dir natively.
    agent_dirs: dict[str, list[Path]] = {
        "claude": [Path.home() / ".claude" / "skills"],
        "agy": [
            Path.home() / ".gemini" / "config" / "skills",
            Path.home() / ".gemini" / "antigravity-cli" / "skills",
        ],
    }

    native_agents: set[str] = {"codex"}
    agent_aliases: dict[str, str] = {"gemini": "agy", "antigravity": "agy"}

    # Manifest file for tracking installed skills
    manifest_file: str = "manifest.json"

    # Security scanner threshold (0.0-1.0)
    security_threshold: float = 0.5

    # Search settings
    search_timeout: float = 15.0
    search_max_results: int = 10
    search_phase1_min_results: int = 3  # Phase 2 triggers when Phase 1 finds fewer

    # Registry API URLs
    smithery_api_url: str = "https://registry.smithery.ai/servers"
    mcp_registry_url: str = "https://registry.modelcontextprotocol.io/v0/servers"
    glama_api_url: str = "https://glama.ai/api/mcp/v1/servers"

    # Authentication tokens (loaded from env / .env — NEVER committed)
    github_token: str = ""

    # Cache settings
    cache_dir: str = ".cache"  # relative to skills_dir
    cache_search_ttl: int = 3600  # 1 hour for search results
    cache_trust_ttl: int = 86400  # 24 hours for trust scores

    # Skills.sh (Vercel) settings — primary registry
    skillssh_enabled: bool = True
    skillssh_npx_path: str = "npx"
    skillssh_github_fallback: bool = True  # GitHub topic search when npx unavailable
    skillssh_search_timeout: float = 30.0  # npx can be slow on first run

    model_config = {"env_prefix": "SKILL_SWARM_", "env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @property
    def python_cmd(self) -> str:
        """Return the best available python command (3.13 > 3.12)."""
        import shutil
        return shutil.which("python3.13") or shutil.which("python3.12") or "python3"

    @property
    def manifest_path(self) -> Path:
        return self.skills_dir / self.manifest_file

    @property
    def cache_path(self) -> Path:
        return self.skills_dir / self.cache_dir

    def skill_dir(self, name: str) -> Path:
        """Return the containing directory for a skill: skills_dir/{name}/"""
        return self.skills_dir / name

    def skill_path(self, name: str) -> Path:
        """Return the canonical path: skills_dir/{name}/SKILL.md"""
        return self.skills_dir / name / "SKILL.md"

    def normalize_agents(self, agents: list[str] | None = None) -> list[str]:
        """Normalize legacy names and return unique supported clients."""
        requested = agents or [*self.agent_dirs.keys(), *sorted(self.native_agents)]
        normalized: list[str] = []
        for agent in requested:
            canonical = self.agent_aliases.get(agent.strip().lower(), agent.strip().lower())
            if canonical in self.agent_dirs or canonical in self.native_agents:
                if canonical not in normalized:
                    normalized.append(canonical)
        return normalized

    def targets_for_agent(self, agent: str) -> list[Path]:
        """Return filesystem discovery targets for a normalized client."""
        canonical = self.agent_aliases.get(agent, agent)
        return self.agent_dirs.get(canonical, [])


settings = Settings(_env_file=os.getenv("SKILL_SWARM_ENV_FILE", ".env"))

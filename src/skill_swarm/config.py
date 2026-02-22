"""Configuration for skill-swarm MCP server."""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Skill-swarm configuration loaded from environment and .env file."""

    # Global skills directory (source of truth) — hidden for cleanliness
    skills_dir: Path = Path.home() / ".agent" / "skills"

    # Agent-specific skill directories (symlink targets)
    agent_dirs: dict[str, Path] = {
        "claude": Path.home() / ".claude" / "skills",
        "gemini": Path.home() / ".gemini" / "skills",
    }

    # Manifest file for tracking installed skills
    manifest_file: str = "manifest.json"

    # Security scanner threshold (0.0-1.0)
    security_threshold: float = 0.5

    # Search settings
    search_timeout: float = 15.0
    search_max_results: int = 10

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
    def manifest_path(self) -> Path:
        return self.skills_dir / self.manifest_file

    @property
    def cache_path(self) -> Path:
        return self.skills_dir / self.cache_dir

    def skill_dir(self, name: str) -> Path:
        """Return the containing directory for a skill: skills_dir/{name}/"""
        return self.skills_dir / name

    def skill_path(self, name: str) -> Path:
        """Return the canonical path: skills_dir/{name}/skill.md"""
        return self.skills_dir / name / "skill.md"


settings = Settings()

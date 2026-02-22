"""Data models for skill-swarm."""

from pydantic import BaseModel, Field


class SkillInfo(BaseModel):
    """Metadata for an installed skill."""

    name: str
    description: str = ""
    version: str = "0.1.0"
    tags: list[str] = Field(default_factory=list)
    source: str = ""  # URL or registry name where it was found
    agents: list[str] = Field(default_factory=lambda: ["claude", "gemini"])
    installed_path: str = ""


class TrustScore(BaseModel):
    """Git-quality trust score for a remote skill/repo."""

    score: float = 0.0  # composite 0.0-1.0
    confidence: float = 0.0  # 0.0-1.0 based on available data
    verdict: str = "UNKNOWN"  # TRUST, CAUTION, WARNING, REJECT
    dimensions: dict[str, float] = Field(default_factory=dict)


class SearchResult(BaseModel):
    """A skill found during search."""

    name: str
    description: str = ""
    source: str = (
        ""  # "local", "skillssh", "smithery", "github", "mcp_registry", "glama"
    )
    url: str = ""  # download or repo URL
    relevance: float = 0.0  # 0.0-1.0
    tags: list[str] = Field(default_factory=list)
    trust: TrustScore | None = None  # None for local skills


class InstallResult(BaseModel):
    """Result of a skill installation."""

    skill_name: str
    success: bool
    install_path: str = ""
    agents_linked: list[str] = Field(default_factory=list)
    security_score: float = 1.0
    trust_score: float | None = None
    errors: list[str] = Field(default_factory=list)


class ScanResult(BaseModel):
    """Security scan result."""

    skill_name: str
    passed: bool = True
    score: float = 1.0  # 0.0-1.0
    findings: list[str] = Field(default_factory=list)


class SkillUsageStats(BaseModel):
    """Usage tracking for an installed skill."""

    match_hits: int = 0
    cherry_pick_count: int = 0
    full_read_count: int = 0
    last_used: str = ""  # ISO timestamp
    last_usage_type: str = ""  # match, cherry_pick, full_read
    installed_at: str = ""

    @property
    def primary_usage(self) -> str:
        """Classify how this skill is primarily used."""
        if self.full_read_count > 0:
            return "full"
        if self.cherry_pick_count > 0:
            return "cherry_pick_only"
        if self.match_hits > 0:
            return "match_only"
        return "dead"


class SkillManifest(BaseModel):
    """Global manifest tracking all installed skills."""

    version: str = "1.0.0"
    skills: dict[str, SkillInfo] = Field(default_factory=dict)

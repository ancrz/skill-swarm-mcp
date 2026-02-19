"""Install and uninstall tools for skill management."""

from skill_swarm.core.cache import purge_prefix
from skill_swarm.core.installer import install_skill as _install
from skill_swarm.core.installer import uninstall_skill as _uninstall
from skill_swarm.core.usage import mark_installed, remove_stats
from skill_swarm.models import InstallResult


async def install_skill(
    name: str,
    source: str,
    agents: list[str] | None = None,
) -> InstallResult:
    """Download, validate, and install a skill globally.

    Pipeline: download -> security scan -> install to ~/.agent/skills/ -> symlink to agent dirs.

    Args:
        name: Skill identifier (e.g. "pdf-parser")
        source: URL, GitHub repo, or direct file URL
        agents: Which agents to serve this skill to (default: all configured)

    Returns:
        InstallResult with success status, path, and any errors.
    """
    result = await _install(name=name, source=source, agents=agents)

    if result.success:
        # Track installation and purge search cache (inventory changed)
        mark_installed(name)
        purge_prefix("search")

    return result


async def uninstall_skill(name: str) -> InstallResult:
    """Remove a skill and all its agent symlinks.

    Args:
        name: Skill identifier to remove

    Returns:
        InstallResult with success status.
    """
    result = await _uninstall(name=name)

    if result.success:
        remove_stats(name)
        purge_prefix("search")

    return result

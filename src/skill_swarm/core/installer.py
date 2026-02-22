"""Skill installation pipeline: download, scan, install, symlink."""

import asyncio
import io
import json
import logging
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import httpx

from skill_swarm.config import settings
from skill_swarm.core.scanner import scan_skill
from skill_swarm.models import InstallResult, SkillInfo, SkillManifest

logger = logging.getLogger("skill-swarm.installer")

# Lock registry to prevent concurrent installs of the same skill
_install_locks: dict[str, asyncio.Lock] = {}
_lock_guard = asyncio.Lock()


async def _get_lock(name: str) -> asyncio.Lock:
    async with _lock_guard:
        if name not in _install_locks:
            _install_locks[name] = asyncio.Lock()
        return _install_locks[name]


def load_manifest() -> SkillManifest:
    """Load the global skills manifest."""
    if settings.manifest_path.exists():
        try:
            data = json.loads(settings.manifest_path.read_text())
            return SkillManifest.model_validate(data)
        except Exception as e:
            logger.warning("Failed to load manifest: %s", e)
    return SkillManifest()


def save_manifest(manifest: SkillManifest) -> None:
    """Save the global skills manifest."""
    settings.skills_dir.mkdir(parents=True, exist_ok=True)
    settings.manifest_path.write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )


async def install_skill(
    name: str,
    source: str,
    agents: list[str] | None = None,
) -> InstallResult:
    """Install a skill globally with security scan and symlinks.

    Pipeline:
    1. Download to temp directory
    2. Security scan
    3. Atomic move to ~/agents/skills/
    4. Create symlinks to agent directories
    5. Update manifest
    """
    if agents is None:
        agents = list(settings.agent_dirs.keys())

    lock = await _get_lock(name)
    async with lock:
        skill_filename = "skill.md"
        skill_dir = settings.skill_dir(name)
        final_path = settings.skill_path(name)

        # Check if already installed
        if final_path.exists():
            return InstallResult(
                skill_name=name,
                success=True,
                install_path=str(final_path),
                agents_linked=agents,
                errors=["Already installed (use force to reinstall)"],
            )

        temp_dir = Path(tempfile.mkdtemp(prefix=f"skill-swarm-{name}-"))

        try:
            # Step 1: Download
            temp_file = temp_dir / skill_filename
            downloaded = await _download_skill(source, temp_file, temp_dir)
            if not downloaded:
                return InstallResult(
                    skill_name=name,
                    success=False,
                    errors=[f"Failed to download from: {source}"],
                )

            # Step 2: Security scan
            scan_result = scan_skill(temp_dir, name)
            if not scan_result.passed:
                logger.warning(
                    "Security scan BLOCKED '%s': %s", name, scan_result.findings
                )
                return InstallResult(
                    skill_name=name,
                    success=False,
                    security_score=scan_result.score,
                    errors=[f"Security scan failed: {', '.join(scan_result.findings)}"],
                )

            # Step 3: Atomic move to global skills dir (subdirectory per skill)
            skill_dir.mkdir(parents=True, exist_ok=True)
            if temp_file.exists():
                shutil.copy2(str(temp_file), str(final_path))
            else:
                # If download produced a directory, look for SKILL.md or main .md
                skill_md = _find_skill_md(temp_dir)
                if skill_md:
                    shutil.copy2(str(skill_md), str(final_path))
                else:
                    return InstallResult(
                        skill_name=name,
                        success=False,
                        errors=["No skill markdown file found in downloaded content"],
                    )

            # Step 4: Create symlinks to agent directories
            linked_agents = _create_symlinks(skill_filename, final_path, agents)

            # Step 5: Update manifest
            manifest = load_manifest()
            manifest.skills[name] = SkillInfo(
                name=name,
                description=_extract_description(final_path),
                source=source,
                agents=linked_agents,
                installed_path=str(final_path),
            )
            save_manifest(manifest)

            logger.info(
                "Installed '%s' → %s (linked to: %s)", name, final_path, linked_agents
            )

            return InstallResult(
                skill_name=name,
                success=True,
                install_path=str(final_path),
                agents_linked=linked_agents,
                security_score=scan_result.score,
            )

        except Exception as e:
            logger.error("Installation failed for '%s': %s", name, e)
            return InstallResult(
                skill_name=name,
                success=False,
                errors=[str(e)],
            )

        finally:
            # Cleanup temp
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)


async def uninstall_skill(name: str) -> InstallResult:
    """Remove a skill and its symlinks from all agents."""
    skill_dir = settings.skill_dir(name)
    skill_path = settings.skill_path(name)

    if not skill_path.exists():
        return InstallResult(
            skill_name=name,
            success=False,
            errors=[f"Skill '{name}' not found at {skill_path}"],
        )

    # Remove symlinks from all agent dirs (directory symlinks)
    for agent_name, agent_dir in settings.agent_dirs.items():
        link_path = agent_dir / name
        if link_path.is_symlink():
            link_path.unlink(missing_ok=True)
            logger.info("Removed symlink: %s", link_path)
        elif link_path.exists():
            shutil.rmtree(link_path, ignore_errors=True)
            logger.info("Removed directory: %s", link_path)

    # Remove the entire skill directory
    shutil.rmtree(skill_dir, ignore_errors=True)

    # Update manifest
    manifest = load_manifest()
    manifest.skills.pop(name, None)
    save_manifest(manifest)

    logger.info("Uninstalled '%s'", name)
    return InstallResult(skill_name=name, success=True)


async def _download_skill(source: str, target_file: Path, temp_dir: Path) -> bool:
    """Download a skill from a URL, local path, or git repo."""
    source = source.strip()

    # Local file path (absolute or file:// URI)
    local_path = None
    if source.startswith("file://"):
        local_path = Path(source[7:])
    elif source.startswith("/"):
        local_path = Path(source)

    if local_path is not None:
        if local_path.is_file():
            # Local ZIP files need extraction, not raw copy
            if local_path.suffix == ".zip":
                try:
                    with zipfile.ZipFile(local_path) as zf:
                        zf.extractall(temp_dir)
                    logger.info("Extracted local ZIP: %s → %s", local_path, temp_dir)
                    return True
                except Exception as e:
                    logger.error("Failed to extract local ZIP: %s", e)
                    return False
            shutil.copy2(str(local_path), str(target_file))
            logger.info("Copied local file: %s → %s", local_path, target_file)
            return True
        elif local_path.is_dir():
            shutil.copytree(
                str(local_path), str(temp_dir / "source"), dirs_exist_ok=True
            )
            return True
        else:
            logger.error("Local path not found: %s", local_path)
            return False

    # Direct markdown URL
    if source.endswith(".md"):
        return await _download_file(source, target_file)

    # ZIP URL
    if source.endswith(".zip"):
        return await _download_and_extract_zip(source, temp_dir)

    # GitHub repo URL
    if "github.com" in source:
        return await _clone_repo(source, temp_dir)

    # GitHub short-ref (owner/repo) commonly returned by skills.sh
    if re.match(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$", source):
        github_url = f"https://github.com/{source}"
        logger.info("Expanded short-ref '%s' to '%s'", source, github_url)
        return await _clone_repo(github_url, temp_dir)

    # Try as raw URL
    return await _download_file(source, target_file)


async def _download_file(url: str, target: Path) -> bool:
    """Download a single file."""
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            target.write_bytes(response.content)
            logger.info("Downloaded: %s → %s", url, target)
            return True
    except Exception as e:
        logger.error("Download failed: %s", e)
        return False


async def _download_and_extract_zip(url: str, target_dir: Path) -> bool:
    """Download ZIP and extract to target directory."""
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
                zf.extractall(target_dir)
            logger.info("Extracted ZIP: %s → %s", url, target_dir)
            return True
    except Exception as e:
        logger.error("ZIP download/extract failed: %s", e)
        return False


async def _clone_repo(url: str, target_dir: Path) -> bool:
    """Clone a git repository."""
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "clone",
            "--depth",
            "1",
            url,
            str(target_dir / "repo"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            logger.error("Git clone failed: %s", stderr.decode())
            return False
        logger.info("Cloned: %s", url)
        return True
    except Exception as e:
        logger.error("Git clone error: %s", e)
        return False


def _create_symlinks(filename: str, source: Path, agents: list[str]) -> list[str]:
    """Create symlinks from agent skill dirs to the source directory.

    Symlinks the entire skill directory ({name}/) so agents see:
        ~/.claude/skills/{name}/skill.md
    """
    # source is the .skill.md file; parent is the skill directory
    skill_dir = source.parent
    skill_dir_name = skill_dir.name

    linked: list[str] = []
    for agent_name in agents:
        agent_dir = settings.agent_dirs.get(agent_name)
        if agent_dir is None:
            continue
        agent_dir.mkdir(parents=True, exist_ok=True)
        link_path = agent_dir / skill_dir_name
        # Remove existing symlink/directory
        if link_path.is_symlink() or link_path.exists():
            if link_path.is_symlink():
                link_path.unlink()
            else:
                shutil.rmtree(link_path, ignore_errors=True)
        link_path.symlink_to(skill_dir)
        linked.append(agent_name)
        logger.info("Symlink: %s → %s", link_path, skill_dir)
    return linked


def _find_skill_md(directory: Path) -> Path | None:
    """Find the main skill markdown file in a directory.

    Searches in order of preference:
    1. SKILL.md (skills.sh standard — uppercase)
    2. skills/ subdirectory tree (skills.sh multi-skill repos)
    3. *.skill.md (legacy naming)
    4. Any .md file (fallback)
    """
    # Direct SKILL.md in root
    root_skill = directory / "SKILL.md"
    if root_skill.exists():
        return root_skill

    # Recursive SKILL.md (handles skills.sh repo format: skills/{name}/SKILL.md)
    for md in directory.rglob("SKILL.md"):
        return md
    # Then any .skill.md
    for md in directory.rglob("*.skill.md"):
        return md
    # Then any .md (but not common non-skill files)
    for md in directory.rglob("*.md"):
        if md.name.upper() not in {
            "README.MD",
            "CHANGELOG.MD",
            "LICENSE.MD",
            "CONTRIBUTING.MD",
            "CODE_OF_CONDUCT.MD",
        }:
            return md
    return None


def _extract_description(skill_path: Path) -> str:
    """Extract description from skill file frontmatter."""
    import re

    try:
        content = skill_path.read_text(encoding="utf-8")
        fm_match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
        if fm_match:
            desc_match = re.search(
                r"^description\s*:\s*(.+)$", fm_match.group(1), re.MULTILINE
            )
            if desc_match:
                val = desc_match.group(1).strip().strip("'\"")
                return val[:200]
    except Exception:
        pass
    return ""

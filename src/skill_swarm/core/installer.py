"""Skill installation pipeline: download, scan, install, symlink."""

import asyncio
import hashlib
import io
import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import httpx
import yaml

from skill_swarm.config import settings
from skill_swarm.core.scanner import scan_skill
from skill_swarm.models import InstallResult, SkillInfo, SkillManifest

logger = logging.getLogger("skill-swarm.installer")

# Lock registry to prevent concurrent installs of the same skill
_install_locks: dict[str, asyncio.Lock] = {}
_lock_guard = asyncio.Lock()


def migrate_legacy_skills_dir() -> int:
    """Move non-conflicting entries from ~/.agent/skills to ~/.agents/skills.

    The singular directory was used by older skill-swarm releases. The plural
    directory is the current Agent Skills user scope used directly by Codex.
    Existing destination entries always win, making this migration idempotent.
    """
    legacy = settings.legacy_skills_dir
    canonical = settings.skills_dir
    if not legacy.exists() or legacy.resolve() == canonical.resolve():
        return 0

    canonical.mkdir(parents=True, exist_ok=True)
    migrated = 0
    for entry in legacy.iterdir():
        destination = canonical / entry.name
        if destination.exists() or destination.is_symlink():
            logger.warning("Legacy migration skipped existing destination: %s", destination)
            continue
        shutil.move(str(entry), str(destination))
        migrated += 1
    return migrated


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


def normalize_skill_filenames() -> int:
    """Migrate lowercase skill.md files to SKILL.md (uppercase).

    Scans all subdirectories of settings.skills_dir and renames any
    skill.md → SKILL.md where lowercase exists and uppercase does NOT.
    Updates installed_path in manifest for affected skills.

    Returns the number of files renamed. Idempotent — safe to call repeatedly.
    """
    skills_dir = settings.skills_dir
    if not skills_dir.exists():
        return 0

    renames = 0
    affected_skills: list[str] = []

    for child in skills_dir.iterdir():
        # Skip non-directories and hidden dirs (.cache, etc.)
        if not child.is_dir() or child.name.startswith("."):
            continue

        lowercase = child / "skill.md"
        uppercase = child / "SKILL.md"

        if lowercase.exists() and not uppercase.exists():
            lowercase.rename(uppercase)
            renames += 1
            affected_skills.append(child.name)
            logger.info("Renamed %s → %s", lowercase, uppercase)

    # Update manifest installed_path for any renamed skills
    if affected_skills:
        manifest = load_manifest()
        for skill_name in affected_skills:
            if skill_name in manifest.skills:
                manifest.skills[skill_name].installed_path = str(
                    skills_dir / skill_name / "SKILL.md"
                )
        save_manifest(manifest)

    return renames


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
    agents = settings.normalize_agents(agents)

    lock = await _get_lock(name)
    async with lock:
        skill_filename = "SKILL.md"
        skill_dir = settings.skill_dir(name)
        final_path = settings.skill_path(name)

        # Check if already installed
        if final_path.exists():
            linked_agents = _create_symlinks(skill_filename, final_path, agents)
            manifest = load_manifest()
            existing = manifest.skills.get(name)
            if existing:
                existing.agents = linked_agents
                existing.installed_path = str(final_path)
            else:
                manifest.skills[name] = SkillInfo(
                    name=name,
                    description=_extract_description(final_path),
                    source=source,
                    agents=linked_agents,
                    installed_path=str(final_path),
                )
            save_manifest(manifest)
            return InstallResult(
                skill_name=name,
                success=True,
                install_path=str(final_path),
                agents_linked=linked_agents,
                errors=["Already installed; client links reconciled"],
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

            # Step 3: Validate the Agent Skills entrypoint and atomically install
            # its complete folder, including scripts/references/assets.
            skill_md = temp_file if temp_file.exists() else _find_skill_md(temp_dir)
            if not skill_md:
                return InstallResult(
                    skill_name=name,
                    success=False,
                    errors=["No SKILL.md found in downloaded content"],
                )
            metadata_error = _validate_skill_metadata(skill_md)
            if metadata_error:
                return InstallResult(
                    skill_name=name,
                    success=False,
                    errors=[metadata_error],
                )
            _install_skill_tree(skill_md.parent, skill_dir)

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

    # Remove only managed symlinks that resolve to this canonical skill. Never
    # delete a real client directory or a symlink owned by another installer.
    warnings: list[str] = []
    canonical_target = skill_dir.resolve()
    for agent_name, agent_dirs in settings.agent_dirs.items():
        for agent_dir in agent_dirs:
            link_path = agent_dir / name
            if link_path.is_symlink():
                try:
                    link_target = link_path.resolve(strict=False)
                except OSError:
                    link_target = None
                if link_target == canonical_target:
                    link_path.unlink(missing_ok=True)
                    logger.info("Removed symlink: %s", link_path)
                else:
                    warnings.append(f"Skipped unmanaged symlink: {link_path}")
            elif link_path.exists():
                warnings.append(f"Skipped unmanaged directory: {link_path}")

    # Remove the entire skill directory
    shutil.rmtree(skill_dir, ignore_errors=True)

    # Update manifest
    manifest = load_manifest()
    manifest.skills.pop(name, None)
    save_manifest(manifest)

    logger.info("Uninstalled '%s'", name)
    return InstallResult(skill_name=name, success=True, errors=warnings)


async def update_skill(name: str) -> InstallResult:
    """Update an installed skill from its original source.

    Downloads latest version, compares SHA-256 hashes, and atomically
    replaces if content changed. Symlinks are preserved (they point to
    the directory, not the file).
    """
    manifest = load_manifest()

    if name not in manifest.skills:
        return InstallResult(
            skill_name=name, success=False,
            errors=[f"Skill '{name}' is not installed"],
        )

    info = manifest.skills[name]
    source = info.source

    if not source:
        return InstallResult(
            skill_name=name, success=False,
            errors=[f"Skill '{name}' has no source URL recorded — cannot update"],
        )

    # Validate local source paths
    if source.startswith("/") or source.startswith("file://"):
        local_path = source.replace("file://", "")
        if not Path(local_path).exists():
            return InstallResult(
                skill_name=name, success=False,
                errors=[f"Source path no longer exists: {source}"],
            )

    installed_path = settings.skill_path(name)
    if not installed_path.exists():
        return InstallResult(
            skill_name=name, success=False,
            errors=[f"Installed file not found at {installed_path}"],
        )

    # Stage temp download inside skills_dir for os.replace atomicity
    temp_dir = settings.skills_dir / f".update-{name}"
    try:
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_file = temp_dir / "SKILL.md"

        # Download new version
        await _download_skill(source, temp_file, temp_dir)

        # Find SKILL.md in downloaded content
        downloaded = _find_skill_md(temp_dir)
        if not downloaded:
            return InstallResult(
                skill_name=name, success=False,
                errors=[f"No SKILL.md found in downloaded content from {source}"],
            )

        # Security scan
        scan_result = scan_skill(temp_dir, name)
        if not scan_result.passed:
            return InstallResult(
                skill_name=name, success=False,
                security_score=scan_result.score,
                errors=[f"Security scan failed: {', '.join(scan_result.findings)}"],
            )

        metadata_error = _validate_skill_metadata(downloaded)
        if metadata_error:
            return InstallResult(
                skill_name=name,
                success=False,
                errors=[metadata_error],
            )

        # Compare complete skill trees so supporting resource changes update too.
        old_hash = _tree_hash(installed_path.parent)
        new_hash = _tree_hash(downloaded.parent)

        if old_hash == new_hash:
            return InstallResult(
                skill_name=name, success=True,
                install_path=str(installed_path),
                security_score=scan_result.score,
                errors=["Already up to date (content unchanged)"],
            )

        # Atomic directory replacement, including supporting resources.
        _replace_skill_tree(downloaded.parent, installed_path.parent)

        # Update manifest description
        new_desc = _extract_description(installed_path)
        if new_desc:
            manifest.skills[name].description = new_desc
        save_manifest(manifest)

        return InstallResult(
            skill_name=name, success=True,
            install_path=str(installed_path),
            agents_linked=info.agents,
            security_score=scan_result.score,
        )
    except Exception as e:
        return InstallResult(
            skill_name=name, success=False,
            errors=[f"Update failed: {e}"],
        )
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


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
        ~/.claude/skills/{name}/SKILL.md
    """
    # source is the .SKILL.md file; parent is the skill directory
    skill_dir = source.parent
    skill_dir_name = skill_dir.name

    linked: list[str] = []
    for agent_name in settings.normalize_agents(agents):
        if agent_name in settings.native_agents:
            if source.exists():
                linked.append(agent_name)
            continue

        targets = settings.targets_for_agent(agent_name)
        target_success = True
        for agent_dir in targets:
            agent_dir.mkdir(parents=True, exist_ok=True)
            link_path = agent_dir / skill_dir_name
            if link_path.is_symlink():
                if link_path.resolve(strict=False) == skill_dir.resolve():
                    continue
                link_path.unlink()
            elif link_path.exists():
                logger.warning("Refusing to replace unmanaged directory: %s", link_path)
                target_success = False
                continue
            link_path.symlink_to(skill_dir)
            logger.info("Symlink: %s → %s", link_path, skill_dir)
        if targets and target_success:
            linked.append(agent_name)
    return linked


def _find_skill_md(directory: Path) -> Path | None:
    """Find the main skill markdown file in a directory.

    Searches in order of preference:
    1. SKILL.md (skills.sh standard — uppercase)
    2. skills/ subdirectory tree (skills.sh multi-skill repos)
    3. *.SKILL.md (legacy naming)
    4. Any .md file (fallback)
    """
    # Direct SKILL.md in root
    root_skill = directory / "SKILL.md"
    if root_skill.exists():
        return root_skill

    # Recursive SKILL.md (handles skills.sh repo format: skills/{name}/SKILL.md)
    for md in directory.rglob("SKILL.md"):
        return md
    # Then any .SKILL.md
    for md in directory.rglob("*.SKILL.md"):
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


def _validate_skill_metadata(skill_path: Path) -> str | None:
    """Validate the required Agent Skills frontmatter fields."""
    try:
        content = skill_path.read_text(encoding="utf-8")
        match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return "SKILL.md must start with YAML frontmatter"
        metadata = yaml.safe_load(match.group(1)) or {}
        if not isinstance(metadata, dict):
            return "SKILL.md frontmatter must be a mapping"
        for field in ("name", "description"):
            if not isinstance(metadata.get(field), str) or not metadata[field].strip():
                return f"SKILL.md frontmatter requires a non-empty '{field}'"
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return f"Invalid SKILL.md metadata: {exc}"
    return None


def _tree_hash(directory: Path) -> str:
    """Return a stable hash of all files in a skill folder."""
    digest = hashlib.sha256()
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        if ".git" in path.parts:
            continue
        digest.update(path.relative_to(directory).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _copy_skill_tree(source_dir: Path, destination: Path) -> None:
    """Copy one skill folder while excluding repository internals."""
    shutil.copytree(
        source_dir,
        destination,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
    )


def _install_skill_tree(source_dir: Path, destination: Path) -> None:
    """Install a new skill directory atomically on the canonical filesystem."""
    settings.skills_dir.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".install-{destination.name}-", dir=settings.skills_dir))
    try:
        _copy_skill_tree(source_dir, stage)
        os.replace(stage, destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def _replace_skill_tree(source_dir: Path, destination: Path) -> None:
    """Replace an installed skill atomically with rollback on failure."""
    stage = Path(tempfile.mkdtemp(prefix=f".update-{destination.name}-", dir=settings.skills_dir))
    backup = settings.skills_dir / f".backup-{destination.name}"
    try:
        _copy_skill_tree(source_dir, stage)
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        os.replace(destination, backup)
        try:
            os.replace(stage, destination)
        except Exception:
            os.replace(backup, destination)
            raise
        shutil.rmtree(backup, ignore_errors=True)
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)

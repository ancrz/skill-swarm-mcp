"""Pattern-based security scanner for skill files."""

import logging
import re
from pathlib import Path

from skill_swarm.models import ScanResult

logger = logging.getLogger("skill-swarm.scanner")

# Patterns that indicate dangerous code in skill files
CRITICAL_PATTERNS: list[tuple[str, str]] = [
    (r"os\.system\s*\([^)]*(?:input|format|%|\{)", "Command injection via dynamic input"),
    (r"subprocess\.(?:run|call|Popen)\s*\([^)]*shell\s*=\s*True", "Shell injection via subprocess"),
    (r"eval\s*\(\s*(?:input|request|os\.environ)", "Code injection via eval"),
    (r"exec\s*\(\s*(?:input|request|compile)", "Code execution via exec"),
    (r"shutil\.rmtree\s*\(\s*['\"]\/['\"]", "Root directory deletion"),
    (r"os\.environ\.get\s*\(\s*['\"](?:PASSWORD|API_KEY|SECRET|TOKEN|PRIVATE)", "Credential harvesting"),
    (r"subprocess\.run\s*\(\s*\[\s*['\"]curl['\"].*(?:password|api_key|secret)", "Data exfiltration via curl"),
    (r"requests\.(?:post|put)\s*\([^)]*(?:password|api_key|secret|token)", "Data exfiltration via HTTP"),
    (r"__import__\s*\(\s*['\"](?:ctypes|socket|http)", "Dynamic import of dangerous modules"),
    (r"open\s*\(\s*['\"](?:/etc/passwd|/etc/shadow)", "Sensitive file access"),
]

# Patterns that are suspicious but not blocking
WARNING_PATTERNS: list[tuple[str, str]] = [
    (r"eval\s*\(", "Use of eval()"),
    (r"exec\s*\(", "Use of exec()"),
    (r"os\.system\s*\(", "Use of os.system()"),
    (r"__import__\s*\(", "Dynamic import"),
]

SCANNABLE_EXTENSIONS = {".py", ".sh", ".bash", ".js", ".ts"}


def scan_skill(skill_path: Path, skill_name: str) -> ScanResult:
    """Scan a skill directory or file for security issues.

    Scans all code files (.py, .sh, .js, .ts) for critical patterns.
    Returns a ScanResult with score and findings.
    """
    findings: list[str] = []
    files_scanned = 0

    target = skill_path if skill_path.is_dir() else skill_path.parent

    # Scan code files
    for file_path in _iter_scannable_files(target):
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            files_scanned += 1

            for pattern, description in CRITICAL_PATTERNS:
                if re.search(pattern, content):
                    rel = file_path.relative_to(target)
                    findings.append(f"CRITICAL: {rel}: {description}")

        except (OSError, UnicodeDecodeError):
            continue

    # Also scan the skill markdown itself for embedded code blocks with issues
    for md_file in target.rglob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8", errors="ignore")
            code_blocks = re.findall(r"```(?:python|bash|sh|js)\n(.*?)```", content, re.DOTALL)
            for block in code_blocks:
                for pattern, description in CRITICAL_PATTERNS:
                    if re.search(pattern, block):
                        rel = md_file.relative_to(target)
                        findings.append(f"CRITICAL (embedded): {rel}: {description}")
        except (OSError, UnicodeDecodeError):
            continue

    # Calculate score
    critical_count = len([f for f in findings if f.startswith("CRITICAL")])
    score = max(0.0, 1.0 - (critical_count * 0.3))
    passed = critical_count == 0 and score >= 0.5

    if findings:
        logger.warning("Security scan for '%s': %d findings", skill_name, len(findings))
    else:
        logger.info("Security scan for '%s': clean (%d files)", skill_name, files_scanned)

    return ScanResult(
        skill_name=skill_name,
        passed=passed,
        score=round(score, 2),
        findings=findings,
    )


def _iter_scannable_files(directory: Path):
    """Yield files with scannable extensions."""
    for ext in SCANNABLE_EXTENSIONS:
        yield from directory.rglob(f"*{ext}")

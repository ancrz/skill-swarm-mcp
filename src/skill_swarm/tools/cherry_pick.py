"""Cherry-pick context extraction from skills.

Like git cherry-pick: extract only specific sections from a skill's content
instead of loading the entire skill into context.
"""

import re

from skill_swarm.config import settings
from skill_swarm.core.usage import record_event


def cherry_pick_context(skill_name: str, sections: list[str]) -> dict:
    """Extract specific sections from a skill's markdown content.

    Parses the skill's markdown structure and returns only the requested
    sections (matched by H2/H3 headers). Useful when only a specific
    procedure or reference is needed without loading the full skill.

    Args:
        skill_name: Name of the installed skill
        sections: List of section names to extract (matched against H2/H3 headers)

    Returns:
        Dictionary with extracted sections and metadata.

    Example:
        cherry_pick_context("docker-ops", ["Rollback", "Health Check"])
        → Returns only those two sections from the docker-ops skill
    """
    skill_path = settings.skill_path(skill_name)

    if not skill_path.exists():
        return {"error": f"Skill '{skill_name}' not found at {skill_path}"}

    content = skill_path.read_text(encoding="utf-8")

    # Parse all sections from the markdown
    all_sections = _parse_sections(content)

    # Match requested sections (case-insensitive, partial match)
    extracted: dict[str, str] = {}
    available_names = list(all_sections.keys())

    for requested in sections:
        matched = _find_best_match(requested, available_names)
        if matched:
            extracted[matched] = all_sections[matched]

    not_found = [s for s in sections if not _find_best_match(s, available_names)]

    # Track cherry-pick usage
    if extracted:
        record_event(skill_name, "cherry_pick")

    return {
        "skill_name": skill_name,
        "sections_requested": sections,
        "sections_extracted": len(extracted),
        "content": extracted,
        "available_sections": available_names,
        "not_found": not_found,
    }


def _parse_sections(markdown: str) -> dict[str, str]:
    """Parse markdown into sections keyed by header text.

    Splits on H2 (##) and H3 (###) headers. Each section includes
    all content until the next header of equal or higher level.
    """
    sections: dict[str, str] = {}

    # Remove frontmatter
    content = re.sub(r"^---\s*\n.*?\n---\s*\n", "", markdown, count=1, flags=re.DOTALL)

    # Split by H2 and H3 headers
    pattern = r"^(#{2,3})\s+(.+)$"
    matches = list(re.finditer(pattern, content, re.MULTILINE))

    for i, match in enumerate(matches):
        header_text = match.group(2).strip()
        start = match.end()

        # Content extends until next header of same or higher level, or end
        if i + 1 < len(matches):
            end = matches[i + 1].start()
        else:
            end = len(content)

        section_content = content[start:end].strip()
        sections[header_text] = section_content

    return sections


def _find_best_match(query: str, candidates: list[str]) -> str | None:
    """Find the best matching section name (case-insensitive, partial)."""
    query_lower = query.lower()

    # Exact match (case-insensitive)
    for c in candidates:
        if c.lower() == query_lower:
            return c

    # Substring match
    for c in candidates:
        if query_lower in c.lower() or c.lower() in query_lower:
            return c

    # Word overlap match — require at least 50% of query words to match (ceiling)
    query_words = set(query_lower.split())
    min_overlap = max(1, -(-len(query_words) // 2))
    best_match = None
    best_overlap = 0

    for c in candidates:
        c_words = set(c.lower().split())
        overlap = len(query_words & c_words)
        if overlap >= min_overlap and overlap > best_overlap:
            best_overlap = overlap
            best_match = c

    return best_match

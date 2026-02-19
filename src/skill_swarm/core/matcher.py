"""Matcher V2 — BM25F + multi-signal scoring for local skill matching.

Replaces the naive word-overlap matcher with a composite scoring system
inspired by search engine ranking: exact match boost, BM25F field-weighted
scoring, Jaccard on tags, and rapidfuzz for typo tolerance.
"""

import logging
import math
import re
from collections import Counter
from pathlib import Path

import yaml
from rapidfuzz import fuzz

from skill_swarm.config import settings
from skill_swarm.models import SearchResult, SkillInfo

logger = logging.getLogger("skill-swarm.matcher")

# Signal weights (sum = 100 for readability; normalized internally)
SIGNAL_WEIGHTS = {
    "exact_match": 30.0,
    "prefix_match": 20.0,
    "phrase_match": 15.0,
    "bm25f": 15.0,
    "jaccard_tags": 10.0,
    "fuzzy_name": 7.0,
    "fuzzy_desc": 3.0,
}

_TOTAL_WEIGHT = sum(SIGNAL_WEIGHTS.values())

# BM25F parameters for small corpus
_BM25_K1 = 1.2
_BM25_B = 0.3  # low length normalization for short documents

# BM25F field boosts
_FIELD_BOOSTS = {
    "name": 3.0,
    "tags": 2.0,
    "description": 1.0,
}


def _tokenize(text: str) -> list[str]:
    """Extract lowercase alphanumeric tokens."""
    return re.findall(r"[a-z0-9]+", text.lower())


class _BM25FIndex:
    """Lightweight BM25F index for a small skill corpus."""

    def __init__(self, skills: list[SkillInfo]):
        self.skills = skills
        self.N = len(skills)

        # Build per-field token lists and stats
        self.field_docs: dict[str, list[list[str]]] = {
            "name": [], "tags": [], "description": [],
        }
        self.field_avgdl: dict[str, float] = {}
        self.df: Counter = Counter()

        for skill in skills:
            name_tokens = _tokenize(skill.name)
            tag_tokens = _tokenize(" ".join(skill.tags))
            desc_tokens = _tokenize(skill.description)

            self.field_docs["name"].append(name_tokens)
            self.field_docs["tags"].append(tag_tokens)
            self.field_docs["description"].append(desc_tokens)

            # Document frequency across all fields (per unique term per doc)
            all_tokens = set(name_tokens + tag_tokens + desc_tokens)
            for t in all_tokens:
                self.df[t] += 1

        for field in self.field_docs:
            lengths = [len(d) for d in self.field_docs[field]]
            self.field_avgdl[field] = sum(lengths) / max(self.N, 1)

    def score(self, query_tokens: list[str], doc_idx: int) -> float:
        """BM25F score for a single document against query tokens."""
        if self.N == 0:
            return 0.0

        total = 0.0
        for term in query_tokens:
            if term not in self.df:
                continue

            # IDF (Lucene BM25 variant)
            df_val = self.df[term]
            idf = math.log(1 + (self.N - df_val + 0.5) / (df_val + 0.5))

            # Weighted TF across fields
            weighted_tf = 0.0
            for field, boost in _FIELD_BOOSTS.items():
                tokens = self.field_docs[field][doc_idx]
                f = tokens.count(term)
                fl = len(tokens)
                avgfl = max(self.field_avgdl[field], 1.0)
                # Length-normalized, boosted TF per field
                weighted_tf += boost * f / (1 - _BM25_B + _BM25_B * fl / avgfl)

            # BM25 saturation
            total += idf * (weighted_tf / (_BM25_K1 + weighted_tf))

        return total


def match_skill(skill: SkillInfo, query: str, bm25_index: _BM25FIndex | None = None, doc_idx: int = 0) -> float:
    """Score a single skill against a query using multi-signal composite.

    Returns normalized score 0.0-1.0.
    """
    query_lower = query.lower().strip()
    query_tokens = _tokenize(query)
    query_token_set = set(query_tokens)

    if not query_tokens:
        return 0.0

    name_lower = skill.name.lower()
    tags_lower = [t.lower() for t in skill.tags]
    desc_lower = skill.description.lower()
    combined_text = f"{name_lower} {' '.join(tags_lower)} {desc_lower}"

    signals: dict[str, float] = {}

    # Signal 1: Exact match — query IS the skill name
    signals["exact_match"] = 1.0 if query_lower == name_lower else 0.0

    # Signal 2: Prefix match — skill name starts with query, or query contains skill name
    if name_lower.startswith(query_lower):
        signals["prefix_match"] = 1.0
    elif query_lower in name_lower:
        signals["prefix_match"] = 0.7
    elif name_lower in query_lower:
        signals["prefix_match"] = 0.5
    else:
        signals["prefix_match"] = 0.0

    # Signal 3: Phrase match — query found as substring in any field
    signals["phrase_match"] = 1.0 if query_lower in combined_text else 0.0

    # Signal 4: BM25F — field-weighted relevance
    if bm25_index:
        raw_bm25 = bm25_index.score(query_tokens, doc_idx)
        signals["bm25f"] = min(raw_bm25 / 5.0, 1.0)  # normalize to ~0-1
    else:
        # Fallback: simple token overlap ratio
        combined_tokens = set(_tokenize(combined_text))
        overlap = len(query_token_set & combined_tokens)
        signals["bm25f"] = overlap / len(query_tokens)

    # Signal 5: Jaccard on tags
    tag_token_set = set(_tokenize(" ".join(tags_lower)))
    intersection = len(query_token_set & tag_token_set)
    union = len(query_token_set | tag_token_set)
    signals["jaccard_tags"] = intersection / union if union > 0 else 0.0

    # Signal 6: Fuzzy name match (rapidfuzz — typo tolerance)
    signals["fuzzy_name"] = fuzz.ratio(query_lower, name_lower) / 100.0

    # Signal 7: Fuzzy partial match on description
    signals["fuzzy_desc"] = fuzz.partial_ratio(query_lower, desc_lower) / 100.0

    # Weighted composite
    score = sum(SIGNAL_WEIGHTS[k] * signals[k] for k in SIGNAL_WEIGHTS) / _TOTAL_WEIGHT

    return round(score, 4)


def match_skills_local(query: str, threshold: float = 0.3) -> list[SearchResult]:
    """Score all installed local skills against a query.

    Returns skills above threshold, sorted by relevance (descending).
    """
    skills_dir = settings.skills_dir
    if not skills_dir.exists():
        return []

    # Parse all skill.md files (subdirectory structure: {name}/skill.md)
    skill_files = sorted(skills_dir.glob("*/skill.md"))
    skills: list[SkillInfo] = []
    for sf in skill_files:
        info = _parse_skill_file(sf)
        if info:
            skills.append(info)

    if not skills:
        return []

    # Build BM25F index for the corpus
    bm25_index = _BM25FIndex(skills)

    # Score each skill
    results: list[SearchResult] = []
    for idx, skill in enumerate(skills):
        score = match_skill(skill, query, bm25_index=bm25_index, doc_idx=idx)

        if score >= threshold:
            results.append(SearchResult(
                name=skill.name,
                description=skill.description,
                source="local",
                url=str(skills_dir / skill.name / "skill.md"),
                relevance=round(score, 3),
                tags=skill.tags,
            ))

    results.sort(key=lambda r: r.relevance, reverse=True)
    return results


def _parse_skill_file(path: Path) -> SkillInfo | None:
    """Parse YAML frontmatter from a .skill.md file."""
    try:
        content = path.read_text(encoding="utf-8")
        fm_match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
        if not fm_match:
            return None

        meta = yaml.safe_load(fm_match.group(1))
        if not isinstance(meta, dict):
            return None

        name = meta.get("name", path.parent.name)
        tags = meta.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]

        return SkillInfo(
            name=name,
            description=meta.get("description", ""),
            version=str(meta.get("version", "0.1.0")),
            tags=tags,
            installed_path=str(path),
        )
    except Exception as e:
        logger.warning("Failed to parse %s: %s", path.name, e)
        return None

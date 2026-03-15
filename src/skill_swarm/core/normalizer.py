"""Query normalizer — strips stopwords to improve matching quality.

AI agents often send full sentences like "I need a tool that can process PDFs"
instead of keywords like "pdf processing". This module normalizes such queries
by removing common English stopwords, lowercasing, and collapsing whitespace.
"""

import logging
import re

logger = logging.getLogger(__name__)

# High-confidence English stopwords. Excludes ambiguous tech terms (go, r, c).
_STOPWORDS: frozenset[str] = frozenset({
    # Articles & pronouns
    "a", "an", "the", "i", "me", "my", "we", "our", "you", "your",
    "he", "she", "it", "its", "they", "them", "their",
    # Be / have / do
    "is", "am", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did",
    # Modals
    "will", "would", "shall", "should", "can", "could", "may", "might", "must",
    # Relative / interrogative
    "that", "which", "who", "whom", "this", "these", "those", "what", "how",
    # Prepositions
    "for", "to", "from", "of", "in", "on", "at", "by", "with", "about",
    "into", "through", "between",
    # Conjunctions & negation
    "and", "or", "but", "nor", "not", "no", "so", "if", "then", "than",
    "when", "where", "why",
    # Filler verbs & generic words commonly used by AI agents
    "need", "want", "looking", "find", "help", "use", "using",
    "tool", "tools", "something", "anything",
    "able", "like", "just", "also", "very", "really",
    "some", "any", "please", "think",
    "process", "handle", "work", "working", "way",
})

# Tokenize on whitespace and punctuation (keeps alphanumeric + hyphens)
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def normalize_query(raw_query: str) -> str:
    """Normalize a search query by stripping stopwords and collapsing whitespace.

    Args:
        raw_query: The raw query string (may be a full sentence or keywords).

    Returns:
        Cleaned keyword string. If all tokens are stopwords, returns the
        original query lowercased. Empty/whitespace input returns empty string.
    """
    if not raw_query or not raw_query.strip():
        return ""

    lowered = raw_query.lower()
    tokens = _TOKEN_RE.findall(lowered)

    if not tokens:
        return ""

    filtered = [t for t in tokens if t not in _STOPWORDS]

    # If everything was stripped, return original lowercased to avoid empty results
    if not filtered:
        result = lowered.strip()
        logger.debug("normalize_query: all stopwords — returning original: '%s'", result)
        return result

    result = " ".join(filtered)
    if result != lowered.strip():
        logger.debug("normalize_query: '%s' -> '%s'", raw_query, result)

    return result

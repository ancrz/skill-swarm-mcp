"""Multidimensional skill confidence and health scoring with security veto (SPEC-5)."""

from datetime import UTC, datetime
import math
from typing import Any

DIMENSION_WEIGHTS = {
    "quality": 0.35,      # Static scan score + metadata format integrity
    "reliability": 0.30,  # Match / execution success rate
    "experience": 0.15,   # Log-normalized battle-tested usage count
    "recency": 0.20,      # Freshness decay over time
}

TRUST_BAND = 0.75
CAUTION_BAND = 0.50
WARNING_BAND = 0.25
RECENCY_HALF_LIFE_DAYS = 45


def log_norm(value: int, midpoint: int = 10) -> float:
    """Normalize usage count logarithmically to [0.0, 1.0]."""
    if value <= 0:
        return 0.0
    return min(math.log10(1 + value) / math.log10(1 + midpoint), 1.0)


def recency_decay(days: float) -> float:
    """Calculate exponential freshness decay based on half-life."""
    return math.exp(-math.log(2) / RECENCY_HALF_LIFE_DAYS * max(0.0, days))


def days_since(ts_str: str | None) -> float:
    """Compute elapsed days since an ISO timestamp string."""
    if not ts_str:
        return 0.0
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(ts_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return max(0.0, (datetime.now(UTC) - dt).total_seconds() / 86400.0)
        except (ValueError, TypeError):
            continue
    return 0.0


def calculate_skill_health(
    name: str,
    security_score: float = 1.0,
    security_passed: bool = True,
    match_hits: int = 0,
    full_reads: int = 0,
    last_used: str | None = None,
    symlinks_ok: bool = True,
) -> dict[str, Any]:
    """Compute multidimensional confidence score and health classification."""
    dims: dict[str, float] = {}

    # 1. Quality Dimension (Security + Symlink status)
    q_score = (security_score * 0.7) + (0.3 if symlinks_ok else 0.0)
    dims["quality"] = max(0.0, min(1.0, q_score))

    # 2. Reliability Dimension (Passing scan)
    dims["reliability"] = 1.0 if security_passed else 0.0

    # 3. Experience Dimension (Usage volume)
    total_uses = match_hits + full_reads
    dims["experience"] = log_norm(total_uses, midpoint=10)

    # 4. Recency Dimension (Freshness)
    elapsed = days_since(last_used)
    dims["recency"] = recency_decay(elapsed)

    # Hard Security Veto
    security_veto = (not security_passed) or (security_score < 0.50)

    raw_score = sum(dims[k] * DIMENSION_WEIGHTS[k] for k in dims)
    confidence = len(dims) / len(DIMENSION_WEIGHTS)
    effective_score = 0.0 if security_veto else round(raw_score * confidence, 4)

    if security_veto:
        verdict = "REJECT"
    elif effective_score >= TRUST_BAND:
        verdict = "TRUST"
    elif effective_score >= CAUTION_BAND:
        verdict = "CAUTION"
    elif effective_score >= WARNING_BAND:
        verdict = "WARNING"
    else:
        verdict = "REJECT"

    return {
        "name": name,
        "score": effective_score,
        "raw_score": round(raw_score, 4),
        "confidence": round(confidence, 2),
        "verdict": verdict,
        "security_veto": security_veto,
        "dimensions": {k: round(v, 3) for k, v in dims.items()},
        "total_usage_events": total_uses,
    }

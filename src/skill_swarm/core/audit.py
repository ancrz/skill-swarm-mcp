"""Tamper-evident SHA-256 cryptographic audit ledger and secret masking (SPEC-2)."""

from datetime import UTC, datetime
import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3
from typing import Any

from skill_swarm.config import settings

logger = logging.getLogger("skill-swarm.audit")

GENESIS_HASH = "0" * 64

# Sensitive credential patterns
_BEARER_RE = re.compile(r"Bearer\s+([A-Za-z0-9_\-\.]+)", re.IGNORECASE)
_PEM_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z0-9 ]+PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]+PRIVATE KEY-----")
_SENSITIVE_KEY_RE = re.compile(r"(TOKEN|KEY|SECRET|PASS|AUTH|CRED|PASSWORD|APIKEY)", re.IGNORECASE)
_AWS_KEY_RE = re.compile(r"\b(AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}\b")
_GITHUB_TOKEN_RE = re.compile(r"\b(ghp_[a-zA-Z0-9]{36}|github_pat_[a-zA-Z0-9_]{82})\b")
_GCP_API_KEY_RE = re.compile(r"\bAIza[0-9A-Za-z-_]{35}\b")
_SLACK_TOKEN_RE = re.compile(r"\bxox[baprs]-[0-9a-zA-Z]{10,48}\b")
_JWT_RE = re.compile(r"\beyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\b")


def mask_secrets(text: str) -> str:
    """Mask credentials, tokens, and private keys in an arbitrary string."""
    if not text or not isinstance(text, str):
        return text

    text = _PEM_PRIVATE_KEY_RE.sub("[PRIVATE KEY MASKED]", text)
    text = _BEARER_RE.sub("Bearer [MASKED]", text)
    text = _AWS_KEY_RE.sub("[AWS KEY MASKED]", text)
    text = _GITHUB_TOKEN_RE.sub("[GITHUB TOKEN MASKED]", text)
    text = _GCP_API_KEY_RE.sub("[GCP API KEY MASKED]", text)
    text = _SLACK_TOKEN_RE.sub("[SLACK TOKEN MASKED]", text)
    text = _JWT_RE.sub("[JWT MASKED]", text)

    kv_pattern = re.compile(
        r"([\x27\"]?(?:[A-Za-z0-9_]*(?:TOKEN|KEY|SECRET|PASS|AUTH|CRED|PASSWORD|APIKEY)[A-Za-z0-9_]*)[\x27\"]?\s*[:=]\s*[\x27\"]?)([^\s\x27\",;}{\[\]]+)([\x27\"]?)",
        re.IGNORECASE,
    )
    return kv_pattern.sub(r"\1[MASKED]\3", text)


def sanitize_dict(data: Any, max_depth: int = 10) -> Any:
    """Recursively mask sensitive keys in nested dictionaries and lists."""
    if max_depth <= 0:
        return data

    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            if _SENSITIVE_KEY_RE.search(str(k)):
                sanitized[k] = "[MASKED]"
            else:
                sanitized[k] = sanitize_dict(v, max_depth - 1)
        return sanitized
    elif isinstance(data, list):
        return [sanitize_dict(item, max_depth - 1) for item in data]
    elif isinstance(data, str):
        return mask_secrets(data)
    return data


def get_audit_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open or initialize the audit SQLite database connection."""
    target_path = db_path or settings.audit_db_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target_path))
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_audit_table(conn)
    return conn


def _ensure_audit_table(conn: sqlite3.Connection) -> None:
    """Create the tamper-evident skill_audit_log table and indexes."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS skill_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            skill_name TEXT NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            details_json TEXT NOT NULL,
            prev_hash TEXT NOT NULL,
            entry_hash TEXT NOT NULL,
            agent_id TEXT NOT NULL DEFAULT 'default'
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_skill ON skill_audit_log(skill_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON skill_audit_log(action)")
    conn.commit()


def record_audit_event(
    conn: sqlite3.Connection,
    skill_name: str,
    action: str,
    status: str,
    details: Any = None,
    agent_id: str = "default",
) -> dict[str, Any]:
    """Record an audit event with SHA-256 hash chaining and secret sanitization."""
    _ensure_audit_table(conn)

    cur = conn.cursor()
    cur.execute("SELECT entry_hash FROM skill_audit_log ORDER BY id DESC LIMIT 1")
    last_row = cur.fetchone()
    prev_hash = str(last_row[0]) if last_row and last_row[0] else GENESIS_HASH

    sanitized_details = sanitize_dict(details) if details is not None else {}
    details_json = json.dumps(sanitized_details, sort_keys=True)
    iso_ts = datetime.now(UTC).isoformat()

    raw_payload = f"{prev_hash}|{iso_ts}|{skill_name}|{action}|{status}|{details_json}|{agent_id}"
    entry_hash = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()

    cur.execute(
        """
        INSERT INTO skill_audit_log (
            timestamp, skill_name, action, status, details_json, prev_hash, entry_hash, agent_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (iso_ts, skill_name, action, status, details_json, prev_hash, entry_hash, agent_id),
    )
    conn.commit()

    return {
        "status": "RECORDED",
        "id": cur.lastrowid,
        "entry_hash": entry_hash,
        "prev_hash": prev_hash,
        "timestamp": iso_ts,
    }


def verify_audit_chain(conn: sqlite3.Connection) -> dict[str, Any]:
    """Verify cryptographic integrity of the entire SHA-256 audit ledger."""
    _ensure_audit_table(conn)

    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, timestamp, skill_name, action, status, details_json, prev_hash, entry_hash, agent_id
        FROM skill_audit_log ORDER BY id ASC
        """
    )
    rows = cur.fetchall()

    if not rows:
        return {
            "valid": True,
            "verified": True,
            "total_verified": 0,
            "latest_hash": GENESIS_HASH,
            "message": "Audit chain empty (intact by definition).",
        }

    expected_prev = GENESIS_HASH
    for i, row in enumerate(rows):
        row_id, r_ts, r_skill, r_action, r_status, r_details, r_prev, r_entry, r_agent = row

        if r_prev != expected_prev:
            return {
                "valid": False,
                "verified": False,
                "error": f"Broken chain link at id {row_id}: expected prev_hash '{expected_prev}', got '{r_prev}'",
                "broken_row_id": row_id,
                "total_verified": i,
            }

        raw_payload = f"{r_prev}|{r_ts}|{r_skill}|{r_action}|{r_status}|{r_details}|{r_agent}"
        computed_hash = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()

        if computed_hash != r_entry:
            return {
                "valid": False,
                "verified": False,
                "error": f"Tampering detected at id {row_id}: hash mismatch (computed '{computed_hash}', stored '{r_entry}')",
                "broken_row_id": row_id,
                "total_verified": i,
            }

        expected_prev = r_entry

    return {
        "valid": True,
        "verified": True,
        "total_verified": len(rows),
        "latest_hash": expected_prev,
        "message": f"Cryptographic audit chain intact across {len(rows)} chained entries.",
    }

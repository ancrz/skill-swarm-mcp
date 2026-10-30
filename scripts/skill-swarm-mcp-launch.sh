#!/usr/bin/env bash
# =============================================================================
# skill-swarm-mcp-launch.sh — Unified FastMCP Server Launcher (AUTO-PLUG)
#
# Resolves the optimal Python interpreter dynamically across environments
# without requiring client re-registration if virtualenvs are recreated.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 1. Resolve Python Interpreter (Project venv > Python 3.14 > Python 3.13 > Python 3.12 > System)
if [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
    PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
elif command -v python3.14 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3.14)"
elif command -v python3.13 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3.13)"
elif command -v python3.12 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3.12)"
else
    PYTHON_BIN="$(command -v python3)"
fi

export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export SKILL_SWARM_SKILLS_DIR="${SKILL_SWARM_SKILLS_DIR:-$HOME/.agents/skills}"

cd "$PROJECT_ROOT"
exec "$PYTHON_BIN" -m skill_swarm.server

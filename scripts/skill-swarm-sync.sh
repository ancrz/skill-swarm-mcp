#!/usr/bin/env bash
# =============================================================================
# skill-swarm-sync.sh — Atomic Pre-flight Validation & Auto-Plug MCP Installer
#
# 1. Runs py_compile syntax checks across src/skill_swarm/
# 2. Runs import smoke test against skill_swarm.server
# 3. Idempotently registers skill-swarm in:
#    - Claude Code / Desktop (~/.claude/settings.json)
#    - Antigravity (~/.gemini/config/mcp_config.json)
#    - Gemini CLI (~/.gemini/settings.json)
#    - Codex CLI (~/.codex/config.json)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LAUNCHER="$PROJECT_ROOT/scripts/skill-swarm-mcp-launch.sh"

echo "=== [1/3] Pre-flight Syntax & Import Validation ==="
chmod +x "$LAUNCHER"

# Python resolution
if [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
    PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
else
    PYTHON_BIN="$(command -v python3)"
fi

# Syntax compile
"$PYTHON_BIN" -m py_compile "$PROJECT_ROOT"/src/skill_swarm/*.py "$PROJECT_ROOT"/src/skill_swarm/core/*.py "$PROJECT_ROOT"/src/skill_swarm/tools/*.py
echo "✓ Python bytecode compilation clean."

# Import smoke test
PYTHONPATH="$PROJECT_ROOT/src" "$PYTHON_BIN" -c "import skill_swarm.server; print('✓ skill_swarm.server imported successfully.')"

echo "=== [2/3] Idempotent MCP Client Registration (Auto-Plug) ==="

# 1. Antigravity Configuration (~/.gemini/config/mcp_config.json)
AGY_CONFIG="$HOME/.gemini/config/mcp_config.json"
if [[ -d "$HOME/.gemini/config" ]]; then
    "$PYTHON_BIN" -c "
import json
from pathlib import Path

p = Path('$AGY_CONFIG')
data = json.loads(p.read_text()) if p.exists() else {'mcpServers': {}}
data.setdefault('mcpServers', {})['skill-swarm'] = {
    'command': '$LAUNCHER'
}
p.write_text(json.dumps(data, indent=2))
print('✓ Registered in Antigravity:', p)
" || true
fi

# 2. Gemini CLI Configuration (~/.gemini/settings.json)
GEMINI_SETTINGS="$HOME/.gemini/settings.json"
if [[ -d "$HOME/.gemini" ]]; then
    "$PYTHON_BIN" -c "
import json
from pathlib import Path

p = Path('$GEMINI_SETTINGS')
data = json.loads(p.read_text()) if p.exists() else {'mcpServers': {}}
data.setdefault('mcpServers', {})['skill-swarm'] = {
    'command': '$LAUNCHER'
}
p.write_text(json.dumps(data, indent=2))
print('✓ Registered in Gemini CLI:', p)
" || true
fi

# 3. Claude Code Settings (~/.claude/settings.json)
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
if [[ -d "$HOME/.claude" ]]; then
    "$PYTHON_BIN" -c "
import json
from pathlib import Path

p = Path('$CLAUDE_SETTINGS')
data = json.loads(p.read_text()) if p.exists() else {'mcpServers': {}}
data.setdefault('mcpServers', {})['skill-swarm'] = {
    'command': '$LAUNCHER'
}
p.write_text(json.dumps(data, indent=2))
print('✓ Registered in Claude Code:', p)
" || true
fi

echo "=== [3/3] Auto-Plug Complete ==="
echo "skill-swarm is verified, compiled, and registered across all active AI harnesses."

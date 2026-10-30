#!/usr/bin/env bash
# =============================================================================
# setup.sh — Skill Swarm Autonomous Zero-Dependency Setup & Tooling Provisioner
# Dual-mode bootstrapper, updater, and read-only health auditor.
# Ported from Agent Execution Plane (AEP) clean-slate architecture.
# Supports: Ubuntu / Debian / Linux (POSIX sh / bash / zsh)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# -----------------------------------------------------------------------------
# Color Codes & Terminal Logging
# -----------------------------------------------------------------------------
GREEN="\033[0;32m"
BLUE="\033[0;34m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
NC="\033[0m"

log_step() { echo -e "${BLUE}[STEP]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_err() { echo -e "${RED}[ERR]${NC} $1"; }

show_help() {
    cat << 'HELP'
Usage: ./scripts/setup.sh [OPTIONS]

Options:
  --check               Execute 100% read-only health audit of all components
                        (NO file modifications, exits 0 if healthy, 1 if degraded).
  --update, --upgrade   Update self-provisioned toolchains and venv dependencies.
  --help, -h            Show this help message.

Default (no flags):
  Full idempotent user-space provisioning:
  - Astral uv package manager (zero-root)
  - Standalone hermetic Python 3.12+ (no system Python required)
  - Hermetic virtual environment (.venv)
  - Skill Swarm FastMCP server dependencies
  - Multi-agent client registration (Auto-Plug)
HELP
}

# -----------------------------------------------------------------------------
# Flag Parsing
# -----------------------------------------------------------------------------
MODE="setup"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --check)
            MODE="check"
            shift
            ;;
        --update|--upgrade)
            MODE="update"
            shift
            ;;
        --help|-h)
            show_help
            exit 0
            ;;
        *)
            echo "Unknown flag: $1" >&2
            show_help
            exit 2
            ;;
    esac
done

# -----------------------------------------------------------------------------
# Read-Only Health Check (--check)
# -----------------------------------------------------------------------------
run_check() {
    local DEGRADED=0
    echo "=========================================================="
    echo "  Skill Swarm MCP — Health & Toolchain Audit (--check)"
    echo "=========================================================="

    # 1. uv package manager
    local UV_BIN
    UV_BIN="$(command -v uv 2>/dev/null || echo "$HOME/.local/bin/uv")"
    if [[ -x "$UV_BIN" ]]; then
        local UV_VER
        UV_VER="$("$UV_BIN" --version 2>/dev/null || echo "present")"
        echo -e "[PASS] Package Manager uv: PRESENT ($UV_VER)"
    else
        echo -e "[WARN] Package Manager uv: MISSING (can be auto-installed via ./scripts/setup.sh)"
    fi

    # 2. Hermetic Virtual Environment & Python
    local VENV_PY="$PROJECT_ROOT/.venv/bin/python"
    if [[ -x "$VENV_PY" ]]; then
        local PY_VER
        PY_VER="$("$VENV_PY" --version 2>/dev/null || echo "present")"
        echo -e "[PASS] Hermetic Python venv: PRESENT ($PY_VER at $VENV_PY)"
        
        # Test FastMCP import
        if PYTHONPATH="$PROJECT_ROOT/src" "$VENV_PY" -c "import skill_swarm.server" 2>/dev/null; then
            echo -e "[PASS] Skill Swarm Server Import: HEALTHY"
        else
            echo -e "[FAIL] Skill Swarm Server Import: FAILED"
            DEGRADED=1
        fi
    else
        # Check system python fallback
        if command -v python3 >/dev/null 2>&1; then
            local SYS_PY_VER
            SYS_PY_VER="$(python3 --version)"
            echo -e "[WARN] Hermetic .venv MISSING; system python available ($SYS_PY_VER)"
        else
            echo -e "[FAIL] No Python interpreter found on host (neither .venv nor system python)"
            DEGRADED=1
        fi
    fi

    # 3. Client Auto-Plug Registration
    local PLUG_OK=0
    for cfg in "$HOME/.gemini/config/mcp_config.json" "$HOME/.gemini/settings.json" "$HOME/.claude/settings.json"; do
        if [[ -f "$cfg" ]] && grep -q "skill-swarm" "$cfg" 2>/dev/null; then
            PLUG_OK=$((PLUG_OK + 1))
        fi
    done
    if [[ $PLUG_OK -gt 0 ]]; then
        echo -e "[PASS] Auto-Plug MCP Client Registrations: $PLUG_OK client(s) configured"
    else
        echo -e "[WARN] Auto-Plug MCP Client Registrations: None detected (run ./scripts/skill-swarm-sync.sh)"
    fi

    # 4. Storage Directory Writability (SPEC-1)
    local SKILLS_DIR="${SKILL_SWARM_SKILLS_DIR:-$HOME/.agents/skills}"
    if [[ -w "$SKILLS_DIR" ]] || [[ -w "$(dirname "$SKILLS_DIR")" ]]; then
        echo -e "[PASS] Skills Storage Path: WRITABLE ($SKILLS_DIR)"
    else
        echo -e "[WARN] Skills Storage Path: RESTRICTED/READ-ONLY (will fail-soft to volatile tempdir)"
    fi

    echo "=========================================================="
    if [[ $DEGRADED -eq 0 ]]; then
        echo -e "${GREEN}[RESULT] System Health Status: HEALTHY (Exit 0)${NC}"
        return 0
    else
        echo -e "${RED}[RESULT] System Health Status: DEGRADED (Exit 1)${NC}"
        return 1
    fi
}

if [[ "$MODE" == "check" ]]; then
    run_check
    exit $?
fi

# -----------------------------------------------------------------------------
# Setup & Provisioning Mode (Zero-Root, Zero-Dependency)
# -----------------------------------------------------------------------------
TMP_DIR="$(mktemp -d -t skill_swarm_setup_XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT INT TERM

export PATH="$HOME/.local/bin:$PATH"

echo "=========================================================="
echo "  Skill Swarm MCP — Setup & Hermetic Provisioner"
echo "  Mode: $MODE"
echo "=========================================================="

mkdir -p "$HOME/.local/bin"
mkdir -p "$HOME/.agents/skills"

# -----------------------------------------------------------------------------
# 1. Provision Astral uv (Zero-Root Standalone Rust Toolchain)
# -----------------------------------------------------------------------------
log_step "Verificando gestor Astral uv..."
if ! command -v uv &>/dev/null && [[ ! -x "$HOME/.local/bin/uv" ]]; then
    log_step "uv ausente en el host; descargando binario oficial autónomo (Astral)..."
    curl -LsSf https://astral.sh/uv/install.sh | env CARGO_DIST_FORCE_INSTALL_DIR="$HOME/.local/bin" sh
    export PATH="$HOME/.local/bin:$PATH"
fi

if [[ "$MODE" == "update" ]] && command -v uv &>/dev/null; then
    log_step "Actualizando uv..."
    uv self update 2>/dev/null || true
fi
UV_BIN="$(command -v uv || echo "$HOME/.local/bin/uv")"
log_success "uv operativo: $("$UV_BIN" --version 2>/dev/null || echo 'ok')"

# -----------------------------------------------------------------------------
# 2. Provision Hermetic Standalone Python (No System Python Needed)
# -----------------------------------------------------------------------------
VENV_DIR="$PROJECT_ROOT/.venv"
log_step "Provisionando Python hermético autónomo..."

# Si no hay python o si se solicita update, uv descarga e instala Python standalone en ~/.local/share/uv/python
if ! command -v python3 &>/dev/null && [[ ! -d "$VENV_DIR" ]]; then
    log_step "Host sin Python; instalando Python 3.12 standalone en espacio de usuario..."
    "$UV_BIN" python install 3.12
fi

if [[ "$MODE" == "update" ]] || [[ ! -d "$VENV_DIR" ]]; then
    log_step "Creando entorno virtual hermético en $VENV_DIR..."
    if "$UV_BIN" python find 3.14 &>/dev/null; then
        "$UV_BIN" venv --python 3.14 "$VENV_DIR"
    elif "$UV_BIN" python find 3.13 &>/dev/null; then
        "$UV_BIN" venv --python 3.13 "$VENV_DIR"
    elif "$UV_BIN" python find 3.12 &>/dev/null; then
        "$UV_BIN" venv --python 3.12 "$VENV_DIR"
    else
        log_step "Instalando Python 3.12 standalone vía uv..."
        "$UV_BIN" python install 3.12
        "$UV_BIN" venv --python 3.12 "$VENV_DIR"
    fi
fi

# -----------------------------------------------------------------------------
# 3. Synchronize Dependencies into Hermetic Environment
# -----------------------------------------------------------------------------
log_step "Sincronizando dependencias de FastMCP en el entorno hermético..."
VIRTUAL_ENV="$VENV_DIR" "$UV_BIN" pip install -e "$PROJECT_ROOT"
VIRTUAL_ENV="$VENV_DIR" "$UV_BIN" pip install -q "pytest>=8.0" 2>/dev/null || true

log_success "Entorno hermético listo: $("$VENV_DIR/bin/python" --version)"

# -----------------------------------------------------------------------------
# 4. Auto-Plug Registration
# -----------------------------------------------------------------------------
log_step "Ejecutando sincronización y registro Auto-Plug..."
chmod +x "$PROJECT_ROOT/scripts/skill-swarm-mcp-launch.sh" "$PROJECT_ROOT/scripts/skill-swarm-sync.sh"
"$PROJECT_ROOT/scripts/skill-swarm-sync.sh"

echo "=========================================================="
log_success "Skill Swarm MCP se ha instalado herméticamente con éxito."
echo "=========================================================="

<div align="center">

# Skill Swarm

**MCP server for AI agent skill discovery, installation, and orchestration.**

[![Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.13+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![MCP](https://img.shields.io/badge/MCP-Protocol-black?style=flat-square)](https://modelcontextprotocol.io)
[![8 Tools](https://img.shields.io/badge/Tools-8-green?style=flat-square)]()

</div>

---

## The Problem

AI agents (Claude, Gemini, Copilot) need skills to be effective. Today, finding and installing the right MCP server for a task is manual:

1. Search Smithery/GitHub manually
2. Evaluate if the server is trustworthy (stars? license? maintained?)
3. Install and configure it by hand
4. Repeat for every project

**Skill Swarm automates the entire pipeline.** The agent asks "I need to parse PDFs" and Skill Swarm searches 4 registries, evaluates trust, installs the best match, and makes it available instantly.

---

## How It Works

```
Agent asks: "I need database access"
        |
        v
  [match_skills] ──> Local skill found? ──> Use it
        |                                     |
        | No match                            | cherry_pick_context
        v                                     v
  [search_skills] ──> 4 registries       Extract only the
        |              in parallel        sections you need
        v
  [install_skill]
     download -> security scan -> trust check -> install -> symlink
        |
        v
  ~/.agent/skills/{name}/skill.md   <-- Global source of truth
  ~/.claude/skills/{name}/          <-- Symlink (auto)
  ~/.gemini/skills/{name}/          <-- Symlink (auto)
```

### Architecture

```
                    ┌─────────────────────────────────┐
                    │         AI Agent (Claude)        │
                    │   "I need to deploy to Docker"   │
                    └───────────────┬─────────────────┘
                                    │ MCP Protocol (stdio)
                    ┌───────────────▼─────────────────┐
                    │       skill-swarm MCP Server     │
                    │          8 tools, Python 3.13    │
                    ├─────────────────────────────────┤
                    │  Matcher V2   │  Trust Engine    │
                    │  BM25F + 7    │  5 dimensions    │
                    │  signals      │  git-quality     │
                    ├───────────────┼─────────────────┤
                    │  Cache Layer  │  Usage Tracker   │
                    │  TTL file     │  dead skill      │
                    │  1h/24h       │  detection       │
                    └───────┬───────┴────────┬────────┘
                            │                │
              ┌─────────────▼──┐   ┌─────────▼──────────┐
              │  4 Registries  │   │  ~/.agent/skills/   │
              │  MCP Registry  │   │  {name}/skill.md    │
              │  Smithery      │   │                     │
              │  Glama.ai      │   │  Symlinked to:      │
              │  GitHub (+token)│   │  ~/.claude/skills/  │
              └────────────────┘   │  ~/.gemini/skills/  │
                                   └────────────────────┘
```

---

## Quick Start

### Prerequisites

- **Python 3.13+**
- **Git** (for cloning skill sources)
- **GitHub token** (optional, for 5000 req/hr vs 60)

### Installation

```bash
git clone https://github.com/ancrz/skill-swarm.git
cd skill-swarm

# Create virtual environment and install
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e .

# Configure environment
cp .env.example .env
# Edit .env and add your GitHub token
```

### Configure Your AI Agent

Add to your agent's MCP configuration:

**Claude Code** (`~/.claude.json`):

```json
{
  "mcpServers": {
    "skill-swarm": {
      "type": "stdio",
      "command": "/path/to/skill-swarm/.venv/bin/python",
      "args": ["-m", "skill_swarm.server"],
      "env": {
        "SKILL_SWARM_GITHUB_TOKEN": "ghp_your_token_here"
      }
    }
  }
}
```

**Project-level** (`.mcp.json` in your project root):

```json
{
  "mcpServers": {
    "skill-swarm": {
      "command": "/path/to/skill-swarm/.venv/bin/python",
      "args": ["-m", "skill_swarm.server"],
      "env": {
        "SKILL_SWARM_GITHUB_TOKEN": "ghp_your_token_here"
      }
    }
  }
}
```

### Verify

```bash
# Run tests
.venv/bin/python tests/test_core.py

# Test the MCP server responds
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"capabilities":{}}}' | \
  .venv/bin/python -m skill_swarm.server
```

---

## 8 Tools Reference

| Tool | Description | Key Args |
|------|-------------|----------|
| **search_skills** | Search 4 registries with trust scoring | `query`, `scope`, `limit` |
| **match_skills** | BM25F + 7-signal local matching | `task_description`, `threshold` |
| **install_skill** | Download, scan, trust-check, install | `name`, `source`, `agents` |
| **uninstall_skill** | Remove skill + symlinks + tracking | `name` |
| **list_skills** | Inventory with health and usage stats | `agent` |
| **get_skill_info** | Full metadata + content of a skill | `name` |
| **cherry_pick_context** | Extract specific markdown sections | `skill_name`, `sections` |
| **skill_health** | Usage analytics and dead skill detection | *(none)* |

See [TOOLS.md](TOOLS.md) for complete parameter reference and examples.

---

## Skill Directory Compliance

Skills follow the `.agent` standard for cross-agent compatibility:

```
~/.agent/skills/                    # Global source of truth
├── {skill-name}/
│   └── skill.md                    # Skill content (always skill.md)
├── manifest.json                   # Installed skills registry
├── .usage.json                     # Usage tracking data
└── .cache/                         # TTL-based search/trust cache

~/.claude/skills/                   # Agent-specific (symlinks)
├── {skill-name} -> ~/.agent/skills/{skill-name}
└── ...

~/.gemini/skills/                   # Agent-specific (symlinks)
├── {skill-name} -> ~/.agent/skills/{skill-name}
└── ...
```

- The **folder name** identifies the skill
- The **file** is always `skill.md` (standard compliance)
- Agent directories contain **directory symlinks** to the global source
- Any agent that follows the `.agent/skills/` convention can consume skills

---

## Trust Score Engine

Every remote search result includes a trust score (0.0-1.0) computed from 5 git-quality dimensions:

| Dimension | Weight | Signals |
|-----------|--------|---------|
| **Recency** | 0.20 | Exponential decay since last push (half-life: 180 days) |
| **Popularity** | 0.20 | Log-normalized stars, forks, watchers |
| **Maintenance** | 0.25 | Push frequency, open issues ratio |
| **Security** | 0.25 | License trust level (MIT=1.0, GPL=0.5, none=0.1), archived penalty |
| **Completeness** | 0.10 | Description, homepage, topics, README presence |

**Verdicts:**

| Score | Verdict | Action |
|-------|---------|--------|
| >= 0.75 | **TRUST** | Safe to auto-install |
| 0.50-0.74 | **CAUTION** | Show to agent for review |
| 0.25-0.49 | **WARNING** | Manual review recommended |
| < 0.25 | **REJECT** | Block installation |

---

## Matcher V2 — BM25F + Multi-Signal Scoring

Local skill matching uses 7 weighted signals:

| Signal | Weight | Description |
|--------|--------|-------------|
| Exact match | 30 | Query equals skill name |
| Prefix match | 20 | Skill name starts with query |
| Phrase match | 15 | Query found as substring in any field |
| BM25F | 15 | Field-weighted relevance (name=3x, tags=2x, desc=1x) |
| Jaccard tags | 10 | Set similarity on tags |
| Fuzzy name | 7 | Typo-tolerant name matching (rapidfuzz) |
| Fuzzy description | 3 | Partial match on description |

BM25F parameters optimized for small corpus (10-100 skills): k1=1.2, b=0.3.

---

## Platform Support

| Platform | Status | Notes |
|----------|--------|-------|
| **Linux** | Full support | Primary development platform |
| **macOS** | Full support | Same Python ecosystem |
| **Windows** | Compatible | WSL recommended for symlinks |

**AI Agent Compatibility:**

| Agent | Integration | Symlink Dir |
|-------|------------|-------------|
| **Claude Code** | MCP stdio | `~/.claude/skills/` |
| **Gemini** | MCP stdio | `~/.gemini/skills/` |
| **Custom agents** | Add to `agent_dirs` in config | Configurable |

---

## Configuration Reference

All settings are loaded from environment variables (prefix: `SKILL_SWARM_`):

| Variable | Default | Description |
|----------|---------|-------------|
| `SKILL_SWARM_GITHUB_TOKEN` | *(empty)* | GitHub PAT for API access (5000 req/hr) |
| `SKILL_SWARM_CACHE_SEARCH_TTL` | `3600` | Search cache TTL in seconds |
| `SKILL_SWARM_CACHE_TRUST_TTL` | `86400` | Trust score cache TTL in seconds |
| `SKILL_SWARM_SEARCH_TIMEOUT` | `15.0` | HTTP timeout for registry queries |
| `SKILL_SWARM_SEARCH_MAX_RESULTS` | `10` | Max results per search |
| `SKILL_SWARM_SECURITY_THRESHOLD` | `0.5` | Min security scan score to install |

---

## Project Structure

```
skill-swarm/
├── src/skill_swarm/
│   ├── server.py              # FastMCP entry point (8 tools)
│   ├── config.py              # Pydantic Settings + path helpers
│   ├── models.py              # Data models (SkillInfo, TrustScore, etc.)
│   ├── core/
│   │   ├── scanner.py         # Security pattern scanner
│   │   ├── matcher.py         # BM25F + multi-signal scoring
│   │   ├── installer.py       # Download, scan, install pipeline
│   │   ├── registry.py        # 4-registry parallel search
│   │   ├── trust.py           # Git-quality trust scoring engine
│   │   ├── cache.py           # TTL file-based cache
│   │   └── usage.py           # Skill usage tracking
│   └── tools/
│       ├── search.py          # search_skills implementation
│       ├── install.py         # install/uninstall wiring
│       ├── inventory.py       # list/match/get_info wiring
│       └── cherry_pick.py     # Section extraction
├── skill/
│   ├── SKILL.md               # Self-describing skill file
│   └── references/            # Skill reference docs
├── tests/
│   ├── test_core.py           # Unit tests (8)
│   ├── test_e2e_effectiveness.py  # E2E V1 (19 tests)
│   └── test_e2e_v2.py         # E2E V2 — trust, cache, BM25F (15 tests)
├── LICENSE                    # Apache 2.0
├── README.md                  # This file
├── TOOLS.md                   # Complete tool reference
├── pyproject.toml             # Python package config
├── .env.example               # Environment template
├── .mcp.json                  # MCP server config
└── .gitignore
```

---

## Development

```bash
# Install with dev dependencies
pip install -e .

# Run all tests (42 total)
.venv/bin/python tests/test_core.py           # 8 unit tests
.venv/bin/python tests/test_e2e_effectiveness.py  # 19 E2E V1
.venv/bin/python tests/test_e2e_v2.py         # 15 E2E V2

# Test a single search
.venv/bin/python -c "
import asyncio, sys, json
sys.path.insert(0, 'src')
from skill_swarm.tools.search import search_skills
results = asyncio.run(search_skills('filesystem', scope='remote', limit=3))
for r in results:
    trust = r.trust.score if r.trust else 'N/A'
    print(f'{r.name} ({r.source}) trust={trust}')
"
```

---

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.

```
Copyright 2025 Anthony Cruz
```

<div align="center">

**Built with MCP Protocol and AI-assisted engineering.**

</div>

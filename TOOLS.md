# Tools Reference

Complete reference for all 8 tools exposed by the skill-swarm MCP server.

---

## Overview

| Tool                                        | Purpose                                            | Scope          |
| ------------------------------------------- | -------------------------------------------------- | -------------- |
| [search_skills](#search_skills)             | Find skills across 4 registries with trust scoring | Remote + Local |
| [match_skills](#match_skills)               | BM25F + 7-signal matching of installed skills      | Local          |
| [install_skill](#install_skill)             | Download, scan, trust-check, and install globally  | Remote → Local |
| [uninstall_skill](#uninstall_skill)         | Remove skill, symlinks, and tracking data          | Local          |
| [list_skills](#list_skills)                 | Inventory with health, symlinks, and usage stats   | Local          |
| [get_skill_info](#get_skill_info)           | Full metadata, content, and usage of a skill       | Local          |
| [cherry_pick_context](#cherry_pick_context) | Extract specific markdown sections from a skill    | Local          |
| [skill_health](#skill_health)               | Usage analytics and dead skill detection           | Local          |

---

## Recommended Workflow

```
1. match_skills("parse PDFs")          → Check local skills first
2. search_skills("parse PDF", "remote") → No local match? Search registries
3. install_skill("pdf-parser", url)     → Install the best result
4. cherry_pick_context("pdf-parser",    → Use only what you need
     "Quick Start,API Reference")
```

---

## search_skills

Search for skills across 4 remote registries with trust scoring, plus local installed skills.

**Registries searched** (in parallel):

- Official MCP Registry (`registry.modelcontextprotocol.io`)
- Smithery (`registry.smithery.ai`)
- Glama.ai (`glama.ai`)
- GitHub (`api.github.com`) — with optional token for 5000 req/hr

### Parameters

| Parameter | Type      | Default      | Description                                             |
| --------- | --------- | ------------ | ------------------------------------------------------- |
| `query`   | `string`  | _(required)_ | What you want to do (e.g. "parse PDF", "deploy docker") |
| `scope`   | `string`  | `"all"`      | Where to search: `"local"`, `"remote"`, or `"all"`      |
| `limit`   | `integer` | `5`          | Maximum number of results to return                     |

### Returns

JSON array of `SearchResult` objects:

```json
[
  {
    "name": "pdf-tools-mcp",
    "description": "MCP server for PDF manipulation and text extraction",
    "source": "mcp_registry",
    "url": "https://github.com/example/pdf-tools-mcp",
    "relevance": 0.85,
    "tags": ["pdf", "text-extraction"],
    "trust": {
      "score": 0.78,
      "confidence": 0.9,
      "verdict": "TRUST",
      "dimensions": {
        "recency": 0.92,
        "popularity": 0.65,
        "maintenance": 0.8,
        "security": 0.85,
        "completeness": 0.7
      }
    }
  }
]
```

### Fields

| Field         | Type               | Description                                                              |
| ------------- | ------------------ | ------------------------------------------------------------------------ |
| `name`        | `string`           | Skill/server name                                                        |
| `description` | `string`           | What the skill does                                                      |
| `source`      | `string`           | Origin: `"local"`, `"smithery"`, `"github"`, `"mcp_registry"`, `"glama"` |
| `url`         | `string`           | Download URL or repository link                                          |
| `relevance`   | `float`            | Match quality 0.0-1.0                                                    |
| `tags`        | `string[]`         | Categorization tags                                                      |
| `trust`       | `TrustScore\|null` | Git-quality trust score (null for local results)                         |

### Trust Score Dimensions

| Dimension      | Weight | Signals                                                            |
| -------------- | ------ | ------------------------------------------------------------------ |
| `recency`      | 0.20   | Exponential decay since last push (half-life: 180 days)            |
| `popularity`   | 0.20   | Log-normalized stars, forks, watchers                              |
| `maintenance`  | 0.25   | Push frequency, open issues ratio                                  |
| `security`     | 0.25   | License trust level (MIT=1.0, GPL=0.5, none=0.1), archived penalty |
| `completeness` | 0.10   | Description, homepage, topics, README presence                     |

### Trust Verdicts

| Score     | Verdict   | Recommended Action        |
| --------- | --------- | ------------------------- |
| >= 0.75   | `TRUST`   | Safe to auto-install      |
| 0.50-0.74 | `CAUTION` | Show to agent for review  |
| 0.25-0.49 | `WARNING` | Manual review recommended |
| < 0.25    | `REJECT`  | Block installation        |

### Examples

```
# Search everything
search_skills("database access")

# Only search remote registries
search_skills("web scraping", scope="remote", limit=10)

# Only search installed skills
search_skills("docker deploy", scope="local")
```

### Caching

- Search results are cached for **1 hour** (configurable via `SKILL_SWARM_CACHE_SEARCH_TTL`)
- Trust scores are cached for **24 hours** (configurable via `SKILL_SWARM_CACHE_TRUST_TTL`)
- Cache is automatically purged when skills are installed or uninstalled

---

## match_skills

Find installed skills matching a task description using BM25F + multi-signal scoring.

This is the **first tool to call** when you need a skill — check what's already installed before searching remotely.

### Parameters

| Parameter          | Type     | Default      | Description                                          |
| ------------------ | -------- | ------------ | ---------------------------------------------------- |
| `task_description` | `string` | _(required)_ | What you want to accomplish                          |
| `threshold`        | `float`  | `0.05`       | Minimum relevance score (0.0-1.0). Default 0.05 = 5% |

### Returns

JSON array of matched skills:

```json
[
  {
    "name": "docker-ops",
    "description": "Docker container management and deployment operations",
    "relevance_pct": 45.2,
    "source": "https://github.com/example/docker-ops",
    "tags": ["docker", "devops", "deployment"]
  }
]
```

### 7 Scoring Signals

| Signal            | Weight | Description                                          |
| ----------------- | ------ | ---------------------------------------------------- |
| Exact match       | 30     | Query equals skill name                              |
| Prefix match      | 20     | Skill name starts with query                         |
| Phrase match      | 15     | Query found as substring in any field                |
| BM25F             | 15     | Field-weighted relevance (name=3x, tags=2x, desc=1x) |
| Jaccard tags      | 10     | Set similarity on tag sets                           |
| Fuzzy name        | 7      | Typo-tolerant name matching (rapidfuzz)              |
| Fuzzy description | 3      | Partial match on description                         |

### BM25F Parameters

Optimized for small corpus (10-100 skills):

- `k1 = 1.2` — term frequency saturation
- `b = 0.3` — low length normalization (skills are short documents)
- IDF: `log(1 + (N - df + 0.5) / (df + 0.5))`

### Usage Tracking

Each skill returned in match results is tracked as a `match` event in usage analytics.

### Examples

```
# Basic task matching
match_skills("parse PDF files and extract text")

# Broad query with lower threshold
match_skills("deploy", threshold=0.01)

# Strict matching for exact needs
match_skills("kubernetes helm charts", threshold=0.15)
```

---

## install_skill

Download, security-scan, trust-check, and install a skill globally with agent symlinks.

### Pipeline

```
download source → security scan → trust check → install to ~/.agent/skills/{name}/skill.md → symlink to agents → update manifest
```

### Parameters

| Parameter | Type     | Default           | Description                                        |
| --------- | -------- | ----------------- | -------------------------------------------------- |
| `name`    | `string` | _(required)_      | Skill identifier (e.g. `"pdf-parser"`)             |
| `source`  | `string` | _(required)_      | URL (markdown, GitHub repo) or local path          |
| `agents`  | `string` | `"claude,gemini"` | Comma-separated agent names to create symlinks for |

### Accepted Source Formats

| Format              | Example                                                      |
| ------------------- | ------------------------------------------------------------ |
| GitHub repo URL     | `https://github.com/owner/repo`                              |
| Raw file URL        | `https://raw.githubusercontent.com/owner/repo/main/skill.md` |
| Direct markdown URL | `https://example.com/my-skill.md`                            |
| Local file path     | `/home/user/skills/my-skill.md`                              |

### Returns

JSON `InstallResult` object:

```json
{
  "skill_name": "pdf-parser",
  "success": true,
  "install_path": "/home/user/.agent/skills/pdf-parser/skill.md",
  "agents_linked": ["claude", "gemini"],
  "security_score": 0.95,
  "trust_score": 0.78,
  "errors": []
}
```

### Fields

| Field            | Type          | Description                            |
| ---------------- | ------------- | -------------------------------------- |
| `skill_name`     | `string`      | Name used for installation             |
| `success`        | `boolean`     | Whether installation succeeded         |
| `install_path`   | `string`      | Final path of the installed skill file |
| `agents_linked`  | `string[]`    | Agents that received symlinks          |
| `security_score` | `float`       | Security scan result (0.0-1.0)         |
| `trust_score`    | `float\|null` | Trust score if remote source           |
| `errors`         | `string[]`    | Any errors encountered                 |

### Security Scan

The scanner checks for dangerous patterns before installation:

- `eval()`, `exec()`, `os.system()` calls
- Shell injection patterns
- Filesystem destruction (`shutil.rmtree("/")`)
- Network exfiltration patterns
- Obfuscated code

Minimum security score to allow installation: **0.5** (configurable via `SKILL_SWARM_SECURITY_THRESHOLD`)

### Directory Structure After Install

```
~/.agent/skills/
└── pdf-parser/
    └── skill.md              ← Installed file

~/.claude/skills/
└── pdf-parser -> ~/.agent/skills/pdf-parser   ← Directory symlink

~/.gemini/skills/
└── pdf-parser -> ~/.agent/skills/pdf-parser   ← Directory symlink
```

### Side Effects

- Purges search cache (inventory changed)
- Records `installed_at` timestamp in usage tracker
- Updates global manifest at `~/.agent/skills/manifest.json`

### Examples

```
# Install from GitHub
install_skill("dbhub", "https://github.com/bytebase/dbhub")

# Install for specific agents only
install_skill("my-tool", "https://example.com/tool.md", agents="claude")

# Install from local file
install_skill("custom-skill", "/home/user/my-skill.md", agents="claude,gemini")
```

---

## uninstall_skill

Remove a skill, all agent symlinks, and usage tracking data.

### Parameters

| Parameter | Type     | Default      | Description          |
| --------- | -------- | ------------ | -------------------- |
| `name`    | `string` | _(required)_ | Skill name to remove |

### Returns

JSON `InstallResult` object:

```json
{
  "skill_name": "pdf-parser",
  "success": true,
  "install_path": "",
  "agents_linked": [],
  "security_score": 1.0,
  "trust_score": null,
  "errors": []
}
```

### What Gets Removed

1. Skill directory: `~/.agent/skills/{name}/` (entire directory tree)
2. Agent symlinks: `~/.claude/skills/{name}`, `~/.gemini/skills/{name}`
3. Manifest entry in `~/.agent/skills/manifest.json`
4. Usage tracking data for the skill

### Side Effects

- Purges search cache (inventory changed)
- Removes usage stats from `~/.agent/skills/.usage.json`

### Examples

```
# Remove a skill completely
uninstall_skill("pdf-parser")
```

---

## list_skills

List all installed skills with metadata, symlink health status, usage stats, and dead skill detection.

### Parameters

| Parameter | Type     | Default | Description                                              |
| --------- | -------- | ------- | -------------------------------------------------------- |
| `agent`   | `string` | `"all"` | Filter by agent name (`"claude"`, `"gemini"`) or `"all"` |

### Returns

```json
{
  "total": 3,
  "skills_dir": "/home/user/.agent/skills",
  "dead_skills": ["unused-tool"],
  "skills": [
    {
      "name": "docker-ops",
      "description": "Docker container management",
      "version": "1.0.0",
      "source": "https://github.com/example/docker-ops",
      "agents": ["claude", "gemini"],
      "symlinks": {
        "claude": "ok",
        "gemini": "ok"
      },
      "usage": {
        "primary_usage": "full",
        "match_hits": 12,
        "cherry_pick_count": 5,
        "full_read_count": 3,
        "last_used": "2025-06-15T14:30:00"
      }
    }
  ]
}
```

### Symlink Health Status

| Status                      | Meaning                                           |
| --------------------------- | ------------------------------------------------- |
| `"ok"`                      | Symlink exists and resolves to a valid `skill.md` |
| `"broken"`                  | Symlink exists but `skill.md` is missing inside   |
| `"directory (not symlink)"` | Path exists as a real directory, not a symlink    |
| `"missing"`                 | No symlink or directory found for this agent      |

### Dead Skills

The `dead_skills` array lists skills that are installed but have never been matched, read, or cherry-picked. Consider removing these to keep your skill inventory clean.

### Examples

```
# List all skills
list_skills()

# List only Claude-linked skills
list_skills(agent="claude")
```

---

## get_skill_info

Get full metadata, content, symlink status, and usage stats of an installed skill.

Calling this tool counts as a **full_read** in usage tracking.

### Parameters

| Parameter | Type     | Default      | Description           |
| --------- | -------- | ------------ | --------------------- |
| `name`    | `string` | _(required)_ | Skill name to inspect |

### Returns

```json
{
  "name": "docker-ops",
  "path": "/home/user/.agent/skills/docker-ops/skill.md",
  "size_bytes": 4523,
  "content": "---\nname: docker-ops\n...\n# Docker Operations\n...",
  "description": "Docker container management and deployment",
  "version": "1.0.0",
  "tags": ["docker", "devops"],
  "source": "https://github.com/example/docker-ops",
  "symlinks": {
    "claude": "linked",
    "gemini": "linked"
  },
  "usage": {
    "primary_usage": "full",
    "match_hits": 12,
    "cherry_pick_count": 5,
    "full_read_count": 4,
    "last_used": "2025-06-15T14:30:00"
  }
}
```

### Symlink Status Values

| Status                      | Meaning                               |
| --------------------------- | ------------------------------------- |
| `"linked"`                  | Symlink exists and resolves correctly |
| `"directory (not symlink)"` | Real directory, not a symlink         |
| `"not linked"`              | No symlink exists for this agent      |

### Error Response

If the skill is not found:

```json
{
  "error": "Skill 'unknown-skill' not found at /home/user/.agent/skills/unknown-skill/skill.md"
}
```

### Examples

```
# Get full info about a skill
get_skill_info("docker-ops")
```

---

## cherry_pick_context

Extract specific sections from a skill's markdown content — like `git cherry-pick` for context.

Instead of loading an entire skill into the agent's context window, extract only the sections relevant to the current task. Sections are matched against H2 (`##`) and H3 (`###`) markdown headers.

Calling this tool counts as a **cherry_pick** event in usage tracking.

### Parameters

| Parameter    | Type     | Default      | Description                                                    |
| ------------ | -------- | ------------ | -------------------------------------------------------------- |
| `skill_name` | `string` | _(required)_ | Name of the installed skill                                    |
| `sections`   | `string` | _(required)_ | Comma-separated section names (e.g. `"Rollback,Health Check"`) |

### Section Matching

Sections are matched in order of specificity:

1. **Exact match** (case-insensitive): `"Overview"` matches `## Overview`
2. **Substring match**: `"Rollback"` matches `## Rollback Procedure`
3. **Word overlap** (50%+ words): `"Health Check"` matches `## Container Health Checks`

### Returns

````json
{
  "skill_name": "docker-ops",
  "sections_requested": ["Rollback", "Health Check"],
  "sections_extracted": 2,
  "content": {
    "Rollback Procedure": "Steps to rollback a deployment:\n1. ...\n2. ...",
    "Container Health Checks": "Configure health checks:\n```yaml\nhealthcheck:\n  test: curl -f http://localhost/\n```"
  },
  "available_sections": [
    "Overview",
    "Installation",
    "Rollback Procedure",
    "Container Health Checks",
    "Troubleshooting"
  ],
  "not_found": []
}
````

### Fields

| Field                | Type       | Description                             |
| -------------------- | ---------- | --------------------------------------- |
| `skill_name`         | `string`   | The skill inspected                     |
| `sections_requested` | `string[]` | What was requested                      |
| `sections_extracted` | `integer`  | How many sections were found            |
| `content`            | `object`   | Map of section header → section content |
| `available_sections` | `string[]` | All sections available in the skill     |
| `not_found`          | `string[]` | Requested sections that had no match    |

### Error Response

If the skill is not found:

```json
{
  "error": "Skill 'unknown-skill' not found at /home/user/.agent/skills/unknown-skill/skill.md"
}
```

### Examples

```
# Extract two sections
cherry_pick_context("docker-ops", "Rollback,Health Check")

# Get the quick start from a skill
cherry_pick_context("pdf-parser", "Quick Start")

# Multiple specific sections
cherry_pick_context("kubernetes-ops", "Helm Charts,Secrets Management,Scaling")
```

---

## skill_health

Usage analytics dashboard: which skills are active, cherry-pick-only, or dead.

### Parameters

None.

### Returns

```json
{
  "total_tracked": 5,
  "dead_skills": ["unused-tool"],
  "cache": {
    "cache_dir": "/home/user/.agent/skills/.cache",
    "total_entries": 12,
    "expired_entries": 3,
    "active_entries": 9,
    "size_bytes": 24576
  },
  "skills": {
    "docker-ops": {
      "primary_usage": "full",
      "match_hits": 12,
      "cherry_pick_count": 5,
      "full_read_count": 3,
      "last_used": "2025-06-15T14:30:00",
      "installed_at": "2025-06-01T10:00:00"
    },
    "pdf-parser": {
      "primary_usage": "cherry_pick_only",
      "match_hits": 8,
      "cherry_pick_count": 4,
      "full_read_count": 0,
      "last_used": "2025-06-14T09:15:00",
      "installed_at": "2025-06-02T12:00:00"
    },
    "unused-tool": {
      "primary_usage": "dead",
      "match_hits": 0,
      "cherry_pick_count": 0,
      "full_read_count": 0,
      "last_used": "",
      "installed_at": "2025-06-03T08:00:00"
    }
  }
}
```

### Usage Classifications

| Classification       | Criteria                                             | Recommendation                    |
| -------------------- | ---------------------------------------------------- | --------------------------------- |
| `"full"`             | Read complete at least once (`full_read_count > 0`)  | Active — keep installed           |
| `"cherry_pick_only"` | Only partial extractions (`cherry_pick_count > 0`)   | Active — likely a reference skill |
| `"match_only"`       | Appears in matches but never read (`match_hits > 0`) | Review — may not be needed        |
| `"dead"`             | Never matched, read, or cherry-picked                | Remove — wasting space            |

### Cache Stats

| Field             | Description                                        |
| ----------------- | -------------------------------------------------- |
| `cache_dir`       | Location of the cache directory                    |
| `total_entries`   | Total cached items (search results + trust scores) |
| `expired_entries` | Items past their TTL                               |
| `active_entries`  | Items still within TTL                             |
| `size_bytes`      | Total cache size on disk                           |

### Examples

```
# Check overall skill health
skill_health()
```

---

## Data Models

### SkillInfo

Metadata stored in the global manifest for each installed skill.

| Field            | Type       | Default                | Description                        |
| ---------------- | ---------- | ---------------------- | ---------------------------------- |
| `name`           | `string`   | _(required)_           | Skill identifier                   |
| `description`    | `string`   | `""`                   | What the skill does                |
| `version`        | `string`   | `"0.1.0"`              | Semantic version                   |
| `tags`           | `string[]` | `[]`                   | Categorization tags                |
| `source`         | `string`   | `""`                   | URL or registry where it was found |
| `agents`         | `string[]` | `["claude", "gemini"]` | Agents this skill is linked to     |
| `installed_path` | `string`   | `""`                   | Filesystem path of installation    |

### TrustScore

Git-quality trust assessment for remote skills.

| Field        | Type     | Description                                                                            |
| ------------ | -------- | -------------------------------------------------------------------------------------- |
| `score`      | `float`  | Composite trust score 0.0-1.0                                                          |
| `confidence` | `float`  | Data completeness factor 0.0-1.0                                                       |
| `verdict`    | `string` | `TRUST`, `CAUTION`, `WARNING`, or `REJECT`                                             |
| `dimensions` | `object` | Individual dimension scores (recency, popularity, maintenance, security, completeness) |

### SkillUsageStats

Per-skill usage tracking data.

| Field               | Type      | Description                                                       |
| ------------------- | --------- | ----------------------------------------------------------------- |
| `match_hits`        | `integer` | Times appeared in match/search results                            |
| `cherry_pick_count` | `integer` | Times sections were extracted                                     |
| `full_read_count`   | `integer` | Times read completely                                             |
| `last_used`         | `string`  | ISO timestamp of last interaction                                 |
| `last_usage_type`   | `string`  | Type of last event: `match`, `cherry_pick`, `full_read`, `search` |
| `installed_at`      | `string`  | ISO timestamp of installation                                     |

---

## Configuration

All settings use the `SKILL_SWARM_` prefix and can be set via environment variables or `.env` file.

| Variable                               | Default   | Description                                   |
| -------------------------------------- | --------- | --------------------------------------------- |
| `SKILL_SWARM_GITHUB_TOKEN`             | _(empty)_ | GitHub PAT for API access (5000 req/hr vs 60) |
| `SKILL_SWARM_CACHE_SEARCH_TTL`         | `3600`    | Search cache TTL in seconds                   |
| `SKILL_SWARM_CACHE_TRUST_TTL`          | `86400`   | Trust score cache TTL in seconds              |
| `SKILL_SWARM_SEARCH_TIMEOUT`           | `15.0`    | HTTP timeout for registry queries             |
| `SKILL_SWARM_SEARCH_MAX_RESULTS`       | `10`      | Max results per search query                  |
| `SKILL_SWARM_SECURITY_THRESHOLD`       | `0.5`     | Min security scan score to allow installation |
| `SKILL_SWARM_SKILLSSH_ENABLED`         | `true`    | Enable Skills.sh as primary registry          |
| `SKILL_SWARM_SKILLSSH_NPX_PATH`        | `npx`     | Path to npx binary for `skills find`          |
| `SKILL_SWARM_SKILLSSH_GITHUB_FALLBACK` | `true`    | Use GitHub topic search when npx unavailable  |
| `SKILL_SWARM_SKILLSSH_SEARCH_TIMEOUT`  | `30.0`    | Timeout for npx subprocess calls              |

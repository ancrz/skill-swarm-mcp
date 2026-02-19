# Trusted Registries

Sources for skill discovery, their APIs, and trust levels.

## Registry Overview

| Registry | Type | Trust | Rate Limits | Auth |
|---|---|---|---|---|
| Local (`~/agents/skills/`) | Filesystem | Highest | None | None |
| Smithery.ai | Curated MCP | High | Public API | None |
| GitHub | Open source | Medium | 60 req/hr unauthenticated | Optional token |

## Local Registry

**Path**: `~/agents/skills/`
**Format**: `*.skill.md` files with YAML frontmatter
**Manifest**: `~/agents/skills/manifest.json`

No network required. Searched first via `match_skills()`.

## Smithery.ai

**API**: `https://registry.smithery.ai/api/servers`
**Query**: `?q=<search_term>&limit=<n>`

Smithery is the largest curated marketplace for MCP servers. Results include:
- `qualifiedName`: Unique server identifier
- `description`: What the server does
- `homepage`: URL for more information
- `tags`: Categorization tags

### Trust Indicators

- Servers on Smithery are published and indexed
- Check description quality and specificity
- Prefer servers with clear documentation

## GitHub

**API**: `https://api.github.com/search/repositories`
**Query**: `?q=<term>+skill+OR+mcp-server&sort=stars`

GitHub is searched as a fallback. Results may include:
- Personal projects with varying quality
- Well-maintained community tools
- Official organization repositories

### Trust Indicators

- Star count (higher = more community validation)
- Recent commits (active maintenance)
- Organization vs personal repo
- Presence of README, LICENSE, tests

### Known Trusted Organizations

- `modelcontextprotocol` — Official MCP reference implementations
- `anthropics` — Anthropic's official repos
- `github` — GitHub's official MCP server

## Adding Custom Registries

The `SKILL_SWARM_SMITHERY_API_URL` environment variable can be overridden to point to a custom registry that implements the same API format.

For local/corporate registries, skills can be installed directly from URLs:
```
install_skill(name="internal-tool", source="https://internal.corp/skills/tool.skill.md")
```

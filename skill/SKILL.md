---
name: skill-swarm
description: This skill should be used when the agent needs to "find a skill", "install a skill", "search for tools", "what skills do I have", "cherry-pick context from a skill", "extract a section from a skill", "manage agent skills", or when a task requires capabilities not currently available and the agent considers searching for a pre-built skill instead of building from scratch. Acts as the decision protocol for the skill-swarm MCP server tools.
version: 1.0.0
tags: [skill-management, mcp, discovery, cherry-pick, agent-tools]
---

# Skill Swarm — Agent Skill Discovery and Management

This skill acts as the decision controller for the skill-swarm MCP server. It guides the reasoning process for deciding when and how to search, install, and extract context from skills.

## Decision Protocol

When facing a task, follow this decision tree before proceeding:

### Step 1: Check Local Skills

Call `match_skills(task_description)` with a natural language description of the task.

- If a result returns with `relevance_pct >= 60%` → use that skill directly
- If a result returns with `30% <= relevance_pct < 60%` → consider `cherry_pick_context` to extract only the relevant sections
- If no results or all below 30% → proceed to Step 2

### Step 2: Search Remote Registries

Call `search_skills(query, scope="remote")` with keywords describing the needed capability.

- Evaluate results by description relevance and source trustworthiness
- Smithery results (curated MCP servers) are generally higher trust
- GitHub results require more evaluation of the repository quality

### Step 3: Install or Skip

If a suitable remote skill is found:
- Call `install_skill(name, source, agents="claude,gemini")` to install globally
- The installation pipeline handles security scanning automatically
- If the scan fails, the skill is NOT installed (blocked)
- After installation, the skill is available to all configured agents via symlinks

If no suitable skill is found:
- Proceed with the model's built-in knowledge (this is the natural fallback)
- Do not burn tokens on excessive searching — two search attempts maximum

### Step 4: Cherry-Pick When Needed

When only a specific procedure or reference from a skill is needed:

Call `cherry_pick_context(skill_name, sections="Section Name 1,Section Name 2")`

This extracts only the requested sections without loading the entire skill into context. Analogous to `git cherry-pick` — take only what is relevant.

## Architecture

### Global Installation with Symlinks

Skills are installed once to `~/.agent/skills/` (source of truth) and served to each AI model via symlinks:

```
~/.agent/skills/               ← Source of truth (agent-agnostic)
├── skill-name.skill.md

~/.claude/skills/              ← Symlink for Claude
├── skill-name.skill.md → ~/.agent/skills/skill-name.skill.md

~/.gemini/skills/              ← Symlink for Gemini
├── skill-name.skill.md → ~/.agent/skills/skill-name.skill.md
```

### MCP Tools Available

| Tool | Purpose |
|---|---|
| `match_skills` | Score local skills against task (first step) |
| `search_skills` | Search local + remote registries |
| `install_skill` | Download, scan, install, symlink |
| `uninstall_skill` | Remove skill + symlinks |
| `list_skills` | Inventory with health status |
| `get_skill_info` | Full content + metadata |
| `cherry_pick_context` | Extract specific sections |

### Security

All installations pass through a pattern-based security scanner. Skills containing command injection, code execution, credential harvesting, or data exfiltration patterns are automatically blocked.

## Anti-Patterns

- Do NOT search more than twice for the same capability
- Do NOT install skills that duplicate existing local skills
- Do NOT load entire skills when only one section is needed (use cherry-pick)
- Do NOT bypass security scan failures

## Additional Resources

### Reference Files

For detailed protocols and patterns, consult:
- **`references/discovery-protocol.md`** — Complete search and evaluation workflow
- **`references/cherry-pick-patterns.md`** — Partial context extraction strategies
- **`references/trusted-registries.md`** — Registry sources, APIs, and trust levels

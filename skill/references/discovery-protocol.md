# Discovery Protocol

Complete workflow for finding the right skill for a task.

## Phase 1: Local Inventory Check

Before any network request, check what is already installed.

### Tool Call

```
match_skills(task_description="<natural language description of the task>", threshold=0.3)
```

### Interpreting Results

| Relevance | Action |
|---|---|
| 60-100% | Use the skill directly. Load with `get_skill_info(name)` |
| 30-59% | Partial match. Use `cherry_pick_context(skill, sections)` to extract relevant parts |
| 0-29% | No useful match. Proceed to remote search |

### Example

Task: "I need to parse a PDF and extract tables"

```
match_skills("parse PDF extract tables")
→ [{"name": "pdf-parser", "relevance_pct": 75.0, ...}]  # Strong match
```

## Phase 2: Remote Search

When local skills are insufficient, search trusted registries.

### Tool Call

```
search_skills(query="<keywords>", scope="remote", limit=5)
```

### Crafting Good Queries

- Use 2-4 specific keywords, not full sentences
- Include the domain and action: "pdf parse", "docker deploy", "api auth"
- Avoid generic terms: "help", "tool", "utility"

### Evaluating Results

| Source | Trust Level | Notes |
|---|---|---|
| Smithery | High | Curated MCP server registry |
| GitHub | Medium | Check stars, description quality, last update |
| Local | Highest | Already installed and scanned |

### Decision Matrix

- Result has clear description matching your need → Install
- Result is ambiguous or poorly documented → Skip
- Multiple similar results → Choose the one from Smithery or with more stars
- No results after 2 searches → Use model knowledge

## Phase 3: Installation

### Tool Call

```
install_skill(name="skill-name", source="<url>", agents="claude,gemini")
```

### What Happens Internally

1. Download to temporary directory
2. Pattern-based security scan
3. If scan passes: atomic move to `~/agents/skills/`
4. Create symlinks to `~/.claude/skills/` and `~/.gemini/skills/`
5. Update `manifest.json`

### Handling Failures

- **Security scan failed**: The skill contains dangerous patterns. Do NOT retry. Do NOT attempt to install from a different source of the same skill.
- **Download failed**: Check the URL. Try an alternative source if available.
- **Already installed**: The tool returns success with a note. No action needed.

## Phase 4: Verification

After installation, verify the skill is usable:

```
list_skills(agent="claude")
→ Shows the new skill with symlink status "ok"
```

Then use the skill normally or cherry-pick specific sections.

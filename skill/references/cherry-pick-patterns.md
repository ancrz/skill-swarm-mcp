# Cherry-Pick Patterns

Strategies for extracting partial context from skills.

## Concept

Like `git cherry-pick` selects specific commits instead of merging an entire branch, `cherry_pick_context` extracts specific sections from a skill without loading the full document.

This preserves context window budget while getting exactly the information needed.

## When to Cherry-Pick

- Task requires only a specific procedure from a larger skill
- Multiple skills each contribute a piece to the solution
- Context window is constrained and full skill loading is wasteful
- Only a reference table, command list, or config example is needed

## Tool Call

```
cherry_pick_context(skill_name="docker-ops", sections="Rollback,Health Check")
```

### Section Matching

Sections are matched against H2 (`##`) and H3 (`###`) markdown headers:

- **Exact match** (case-insensitive): "Rollback" matches "## Rollback"
- **Substring match**: "Health" matches "## Health Check Procedure"
- **Word overlap**: "check status" matches "## Status Verification Check"

### Response Format

```json
{
  "skill_name": "docker-ops",
  "sections_requested": ["Rollback", "Health Check"],
  "sections_extracted": 2,
  "content": {
    "Rollback Procedure": "Step 1: ...\nStep 2: ...",
    "Health Check": "Run `docker ps` and verify..."
  },
  "available_sections": ["Overview", "Deploy", "Rollback Procedure", "Health Check", "Monitoring"],
  "not_found": []
}
```

## Multi-Skill Cherry-Pick

To combine context from multiple skills, call cherry_pick_context multiple times:

```
# Get deployment steps from one skill
cherry_pick_context("docker-ops", "Deploy")

# Get monitoring setup from another
cherry_pick_context("prometheus-setup", "Alert Rules")

# Combine both to accomplish the task
```

## Anti-Patterns

- Do NOT cherry-pick when you need the full skill (relevance > 60%)
- Do NOT request more than 3-4 sections at once (defeats the purpose)
- Do NOT cherry-pick from skills you haven't verified exist (call `list_skills` first)

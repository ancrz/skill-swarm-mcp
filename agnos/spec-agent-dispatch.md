# Spec: Agent Dispatch Mapping

## Overview

This spec maps Claude Code Swarm agent types to skill-swarm operations, documents how `SearchMetadata` enables future adaptive behavior, and lays groundwork for observability.

---

## Agent Type → skill-swarm Operation Mapping

### Orchestrator Agent

In the Swarm paradigm, the Orchestrator decomposes tasks and routes to specialists.

In skill-swarm, this role is played by `search_remote_phased()`:

- Decomposes search into Phase 1 (high-trust) and Phase 2 (fallback)
- Routes to registry specialists (`search_skillssh`, `search_smithery`, etc.)
- Aggregates outputs with deduplication + trust scoring
- Returns structured results + metadata to the caller

### Specialist Agents

Each registry client is a specialist with bounded scope:

| Specialist Agent         | skill-swarm Function     | Scope / Trust Baseline |
|--------------------------|--------------------------|------------------------|
| Skills.sh Specialist     | `search_skillssh()`      | Agent skills (0.85-0.95) |
| MCP Registry Specialist  | `search_mcp_registry()`  | Curated MCP servers (0.85) |
| Smithery Specialist      | `search_smithery()`      | General MCP registry (0.70) |
| Glama Specialist         | `search_glama()`         | AI tool index (0.65) |
| GitHub Specialist        | `search_github()`        | Raw repository search (0.50) |
| Trust Evaluator          | `evaluate_github_repo()` | Git-quality scoring |

### Aggregator Agent

`search_remote_phased()` acts as aggregator after dispatching to specialists:

1. Collects `list[SearchResult]` from each specialist
2. Deduplicates by composite key (`normalized_name:source`)
3. Applies trust scoring (async GitHub API calls)
4. Re-sorts by `relevance` descending
5. Truncates to `limit`
6. Emits `SearchMetadata` as structured observability output

---

## SearchMetadata as Adaptive Dispatch Enabler

`SearchMetadata` is not just logging — it is the foundation for future adaptive behavior:

### Current Use (v1)

```python
logger.info(
    "Phased search '%s': P1=%d results (%.0fms), P2=%d results (%.0fms), total=%.0fms",
    query[:50], metadata.phase1_results, metadata.phase1_duration_ms,
    metadata.phase2_results, metadata.phase2_duration_ms, metadata.total_duration_ms,
)
```

Metadata is consumed at INFO level for debugging and performance monitoring.

### Future Adaptive Patterns (forward-looking)

#### Pattern 1: Dynamic Threshold Adjustment

```python
# If Phase 1 consistently returns 0 results for a query pattern,
# lower search_phase1_min_results to 0 for that query category
# (always trigger Phase 2 for unknown query domains)
if metadata.all_failed and metadata.phase1_results == 0:
    adjust_threshold_for_query_category(query, new_min=0)
```

#### Pattern 2: Source Health Monitoring

```python
# Track per-source failure rates using metadata
# If skillssh fails consistently (phase1_results drops to 0 even for popular queries),
# flag it for health check and route all queries to Phase 2
source_health_tracker.record(
    source="skillssh",
    results=metadata.phase1_results,
    duration_ms=metadata.phase1_duration_ms,
)
```

#### Pattern 3: Latency-Aware Routing

```python
# If Phase 1 is fast but returns 0 (e.g., skills.sh down),
# skip skills.sh in future requests for this session
if metadata.phase1_duration_ms > 5000 and metadata.phase1_results == 0:
    disable_skillssh_for_session()
```

#### Pattern 4: Query Enrichment Feedback Loop

```python
# Use metadata.all_failed to trigger query reformulation
if metadata.all_failed:
    enriched_query = expand_query_with_synonyms(query)
    results, _ = await search_remote_phased(enriched_query, limit, with_trust=False)
```

---

## Observability Forward Spec

### Logging (implemented)

All phased searches emit at `INFO` level:

```
Phased search 'filesystem': P1=3 results (245ms), P2=0 results (0ms), total=245ms
```

### Metrics (future)

When a metrics sink is added, `SearchMetadata` maps directly to counters/histograms:

| Metric                               | Source Field              | Type      |
|--------------------------------------|---------------------------|-----------|
| `search.phase1.results`              | `metadata.phase1_results` | Counter   |
| `search.phase2.results`              | `metadata.phase2_results` | Counter   |
| `search.phase1.duration_ms`          | `metadata.phase1_duration_ms` | Histogram |
| `search.phase2.duration_ms`          | `metadata.phase2_duration_ms` | Histogram |
| `search.all_failed`                  | `metadata.all_failed`     | Counter   |
| `search.phase2_triggered`            | `len(metadata.phase2_sources) > 0` | Counter |

### Tracing (future)

Each `search_remote_phased()` call maps to a trace span with child spans per registry:

```
[span] search_remote_phased query="filesystem"
  [span] phase1.skillssh    duration=180ms results=2
  [span] phase1.mcp_registry duration=245ms results=1
  [span] phase2.smithery    duration=310ms results=3   ← triggered because P1=3 == min_results
  [span] phase2.glama       duration=290ms results=2
  [span] phase2.github      duration=350ms results=5
  [span] trust_scoring      duration=890ms results=5
```

---

## Integration with Claude Code Pipeline

In the Claude Code orchestrator pipeline (Archon → Ontos → Pragma), skill-swarm itself acts as a tool invoked by Claude Code agents:

```
Claude Code Agent
    → skill-swarm MCP tool: search_skills("pdf parsing")
        → search_remote_phased()
            → Phase 1: skills.sh + MCP Registry  [parallel]
            → Phase 2: smithery + glama + github  [conditional]
            → trust scoring
        → returns list[SearchResult]
    ← Agent receives JSON array (MCP protocol)
```

`SearchMetadata` is internal to skill-swarm and not exposed via MCP. The agent sees only the curated, trust-scored `list[SearchResult]`.

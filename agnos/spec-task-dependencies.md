# Spec: Task Dependency Graph

## Overview

This document maps the vertical and horizontal dependency traces for the two-phase search orchestration implementation. It ensures no layer is broken and no peer module is silently affected.

---

## Vertical Trace

Data flows from configuration through to the MCP consumer. Each layer's contract must hold.

```
config.py
    └── settings.search_phase1_min_results  (NEW field)
            │
            ▼
models.py
    └── SearchMetadata                      (NEW class)
            │
            ▼
core/registry.py
    ├── search_remote()                     (UNTOUCHED — invariant)
    └── search_remote_phased()              (NEW function, uses settings + SearchMetadata)
            │
            ▼
tools/search.py
    ├── search_skills() return type: list[SearchResult]  (UNCHANGED — invariant)
    └── internal call: search_remote_phased()            (replaces search_remote())
            │
            ▼
server.py
    └── Exposes search_skills via MCP tool  (UNTOUCHED — invariant)
```

### Layer Contracts

| Layer              | Contract                                                                 | Status     |
|--------------------|--------------------------------------------------------------------------|------------|
| `config.py`        | Adds `search_phase1_min_results: int = 3`; no existing field changed     | Additive   |
| `models.py`        | Adds `SearchMetadata` after `SearchResult`; no existing model changed    | Additive   |
| `registry.py`      | Adds `search_remote_phased()`; `search_remote()` lines 477-537 untouched | Additive   |
| `tools/search.py`  | Return type `list[SearchResult]` preserved; internal call updated        | Compatible |
| `server.py`        | No changes required; MCP JSON schema unchanged                           | Untouched  |

---

## Horizontal Trace

Modules that share imports, exports, or state with changed files.

### `models.py` peers

| Peer                        | Import from models.py         | Impact of SearchMetadata addition |
|-----------------------------|-------------------------------|-----------------------------------|
| `core/registry.py`          | `SearchResult`                | Import updated to include `SearchMetadata` |
| `tools/search.py`           | `SearchResult`                | No change needed (SearchMetadata not exported to tools) |
| `server.py`                 | `SearchResult`, `InstallResult`, etc. | No change — SearchMetadata is internal |
| `tests/test_core.py`        | `SkillInfo`, `SkillManifest`  | New tests import `SearchMetadata` directly |

### `config.py` peers

| Peer                        | Uses `settings`               | Impact of new field               |
|-----------------------------|-------------------------------|-----------------------------------|
| `core/registry.py`          | `settings.search_*`           | New field consumed in `search_remote_phased()` |
| All other modules           | Other `settings.*` fields     | No impact — field is additive with default |

### `tools/search.py` peers

| Peer                        | Relationship                  | Impact                            |
|-----------------------------|-------------------------------|-----------------------------------|
| `server.py`                 | Calls `search_skills()`       | Return type unchanged — no impact |
| `tests/test_core.py`        | Tests `search_skills()`       | Regression test added             |
| `tests/test_e2e_*.py`       | Test `search_skills()` via MCP| Return type unchanged — no impact |

---

## Dependency Graph (Implementation Order)

```
Task 4: models.py (SearchMetadata)
    │
    ├──► Task 5: config.py (search_phase1_min_results)
    │         │
    │         └──► Task 6: registry.py (search_remote_phased)
    │                   │
    │                   └──► Task 7: tools/search.py (wire search_remote_phased)
    │                               │
    │                               └──► Task 9: tests/test_core.py (4 new tests)
    │
    └──► Tasks 1-3: agnos/ specs (documentation, no code deps)
    └──► Tasks 10-11: TOOLS.md + README.md (documentation, no code deps)
```

Tasks 1-3 and 10-11 are independent of the code tasks and can run in parallel.

---

## Omission Checklist

- [x] `SearchMetadata` added to models
- [x] `search_phase1_min_results` added to config
- [x] `import time` added to registry.py module level
- [x] `SearchMetadata` added to `from skill_swarm.models import ...` in registry.py
- [x] `search_remote_phased` added after `search_remote`, before `_normalize_name`
- [x] `search_remote` import in `tools/search.py` replaced with `search_remote_phased`
- [x] `search_skills()` return type stays `list[SearchResult]`
- [x] `server.py` untouched
- [x] `search_remote()` body (lines 477-537) untouched
- [x] All 4 new tests use `def test_...()` with `asyncio.run()` (not `async def`)
- [x] Tests added to `tests` list in `__main__` block
- [x] `import asyncio` added to `test_core.py` top-level imports

---

## Risk Assessment

| Risk                                     | Mitigation                                            |
|------------------------------------------|-------------------------------------------------------|
| Phase 2 never triggered in tests         | `test_search_remote_phased_graceful_on_nonsense` uses gibberish to force likely Phase 2 trigger |
| `asyncio.gather` exception leaking       | `return_exceptions=True` on both phases               |
| `_normalize_name` called before defined  | Function is defined at line 540, phased function placed at ~539; resolved by Python's module load order |
| Import of `SearchMetadata` missing       | Explicitly added to `from skill_swarm.models import SearchResult, SearchMetadata` |

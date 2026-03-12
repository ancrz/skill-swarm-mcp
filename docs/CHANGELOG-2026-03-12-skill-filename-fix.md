# CHANGELOG — 2026-03-12 — SKILL.md Filename Case Fix

## Root Cause

`config.py:65` was missed during the March 7 migration from flat-file naming
(`skill-name.skill.md`) to subdirectory naming (`{skill-name}/SKILL.md`).
The `Settings.skill_path()` method still returned lowercase `skill.md`,
while `install_skill()` was already writing uppercase `SKILL.md`.

This mismatch caused `skill_path()` to resolve to a non-existent file,
silently breaking every tool that reads skills by path:
- `match_skills` (via `_parse_skill_file`)
- `list_skills` (via symlink health check)
- `uninstall_skill` (via existence check)
- `get_skill_info` (via file read)
- `cherry_pick_context` (via file read)

## Impact

Any skill installed after March 7 was written as `SKILL.md` but looked up
as `skill.md`. All 5 read-path tools returned "not found" or empty results
for locally-installed skills. Install and search (remote) were unaffected.

Three skills on disk (`~/.agent/skills/`) had lowercase `skill.md` files
from pre-migration installs that were never renamed.

## Fix

### Code

| File | Change |
|---|---|
| `src/skill_swarm/config.py` | `skill_path()` returns `SKILL.md` (uppercase) |
| `src/skill_swarm/core/installer.py` | Added `normalize_skill_filenames()` migration function |
| `src/skill_swarm/server.py` | Calls migration on startup (module-level, before tools register) |

### Documentation

| File | Change |
|---|---|
| `README.md` | 5 references: `skill.md` -> `SKILL.md` |
| `TOOLS.md` | 8 references: `skill.md` -> `SKILL.md` (preserved generic source URL examples) |
| `skill/SKILL.md` | Architecture diagram updated from flat-file to subdirectory+symlink structure |

### Tests

| File | Change |
|---|---|
| `tests/test_core.py` | Fixed `test_create_symlinks` to use `SKILL.md` |
| `tests/test_core.py` | Added `test_normalize_skill_filenames` (rename + manifest update + idempotency) |

## Migration Function: `normalize_skill_filenames()`

- Scans all non-hidden subdirectories of `~/.agent/skills/`
- Renames `skill.md` -> `SKILL.md` where lowercase exists and uppercase does NOT
- Updates `installed_path` in `manifest.json` for affected skills
- Idempotent: returns 0 on subsequent calls
- Runs automatically on server startup

## Files Changed

1. `src/skill_swarm/config.py`
2. `src/skill_swarm/core/installer.py`
3. `src/skill_swarm/server.py`
4. `tests/test_core.py`
5. `README.md`
6. `TOOLS.md`
7. `skill/SKILL.md`
8. `docs/CHANGELOG-2026-03-12-skill-filename-fix.md` (this file)

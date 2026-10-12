---
name: opencode-skills
description: "Set up and mirror OpenCode's skill library from Hermes."
version: 1.0.0
author: Fenrie (Hermes Agent)
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenCode, skills, configuration, integration]
    related_skills: [opencode, hermes-agent]
---

# OpenCode Skills Configuration

Use this skill when setting up, extending, or troubleshooting OpenCode's skill library — especially mirroring skills from Hermes or verifying skills load correctly.

## When to Use

- Setting up OpenCode with a skill library for the first time
- Adding new skills to OpenCode (mirrored from Hermes or sourced elsewhere)
- Verifying OpenCode picks up available skills
- Troubleshooting missing or stale skills in OpenCode

## OpenCode Skill System

OpenCode reads skills from folders configured in `skills.paths` inside `~/.config/opencode/opencode.jsonc`:

```jsonc
{
  "skills": {
    "paths": ["$HOME/.config/opencode/skills"]
  }
}
```

Each entry in `paths` is a directory that OpenCode scans recursively for subdirectories containing a `SKILL.md` file. The skill name is the **directory name**, not the `name:` field in frontmatter.

### SKILL.md Format Compatibility

OpenCode's skill loader accepts the same SKILL.md format Hermes uses — YAML frontmatter + Markdown body, with optional `references/`, `templates/`, and `scripts/` directories. Skills copied from Hermes load in OpenCode without modification. No conversion step is needed.

## Setup: Mirror Hermes Skills to OpenCode

### One-time copy

```bash
mkdir -p ~/.config/opencode/skills
cp -r ~/.hermes/skills/* ~/.config/opencode/skills/
```

### Configure OpenCode

Ensure `~/.config/opencode/opencode.jsonc` contains:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "defaults": {
    "provider": "openrouter",
    "model": "anthropic/claude-sonnet-4-6"
  },
  "skills": {
    "paths": ["$HOME/.config/opencode/skills"]
  }
}
```

### Verify skills are loaded

```bash
cd /tmp && opencode run 'List all available skills you can see, just their names. Respond with SKILL_CHECK_OK at the end.'
```

Success criteria:
- Output lists skill names across all categories
- Command exits 0
- No "no skills found" or config error messages

## Sync Drift

Hermes skills live in `~/.hermes/skills/`. OpenCode skills live in `~/.config/opencode/skills/`. These are **separate copies** — adding or updating a skill in Hermes does NOT automatically update OpenCode.

To re-sync after Hermes skill changes:

```bash
cp -r ~/.hermes/skills/* ~/.config/opencode/skills/
```

For ongoing sync, use rsync (preserves structure, skips unchanged files):

```bash
rsync -a --delete ~/.hermes/skills/ ~/.config/opencode/skills/
```

The `--delete` flag removes OpenCode skills that were deleted from Hermes — omit it if you want to allow OpenCode-only skills.

## Configuration Fields Reference

| Field | Type | Purpose |
|-------|------|---------|
| `skills.paths` | `string[]` | Local directories to scan for SKILL.md files |
| `skills.urls` | `string[]` | Remote URLs to fetch skills from (advanced) |
| `instructions` | `string[]` | Additional instruction files or glob patterns to include in prompts |

## Pitfalls

- **Config file location:** OpenCode config is at `~/.config/opencode/opencode.jsonc`, NOT `~/.opencode/config.yaml`. The old `~/.opencode/` path is unused by current OpenCode versions (1.x).
- **Use `$HOME`, not `~`, in config paths:** OpenCode may not expand `~` in config values. Write `$HOME/.config/opencode/skills`, not `~/...`.
- **Skill name = directory name:** OpenCode derives the skill name from the directory containing SKILL.md. A skill at `skills/my-skill/SKILL.md` is called `my-skill` regardless of the `name:` field in frontmatter.
- **Shared copies drift over time:** After the initial copy, Hermes and OpenCode skill directories diverge. Re-copy or rsync after Hermes skill updates — a future session that adds a skill to Hermes and expects it in OpenCode will be confused when it isn't there.
- **Bundled skills are protected:** Skills that ship with Hermes (e.g. `hermes-agent`, `opencode`) in `~/.hermes/skills/` are off-limits for editing. To modify one, use `hermes curator adopt <name>` first.
- **Empty skills directory silently does nothing:** If `skills.paths` points to a directory with no subdirectories containing SKILL.md files, OpenCode loads zero skills — no error, no warning. Verify with the smoke test above.

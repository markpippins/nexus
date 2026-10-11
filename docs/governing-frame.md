# Governing frame

> **Generated file — do not edit by hand.**
> Rendered by `bin/render_governing_frame.py` from `bin/governing-frame.pins.json`.
> CI verifies this file is byte-identical to the rendered output, so a hand edit fails the
> build. To change the frame, change the pins — which is a doctrine change, reviewed like
> any other, and deliberately re-mints the bootstrap hash.

This is the **ratified governing frame**: the rule-set in force for a governed walk, named and pinned by revision. It is a table of contents, not the doctrine itself — the texts below are governed in their own repositories.

It is the `bootstrap` input to the content-addressed doctrine snapshot (`python/peb-kernel/src/peb_kernel/doctrine.py`), so every execution is attributed to the frame that governed it rather than to whatever happened to be loaded at the time.

## Frame identity

- Frame schema version: `1`
- Renderer version: `1`
- Ratified by:
  - `85c6d979-c862-42ae-bd68-945e1b0c6a9a`
  - `c38ea344-f84d-4ffa-93c4-e762d4fb925d`

## Pinned rule-set in force

### `agent-operating-doctrine`

- Target: `/home/codex/dev/AGENTS.md`
- Kind: `workspace-file`
- Pinned revision: `v2.5` (`declared-version`)
- Note: Workspace operating doctrine. Not in a git repo, so it is pinned by its DECLARED version rather than a git SHA. A file edit that does not change the declared version does NOT move the frame.

### `workspace-claude-md`

- Target: `/home/codex/dev/CLAUDE.md`
- Kind: `workspace-file`
- Pinned revision: `workspace-root` (`pinned-locator`)
- Note: Workspace-level companion doctrine.

### `repo-claude-md`

- Target: `CLAUDE.md`
- Kind: `repo-file`
- Pinned revision: `c22cdc1f2f92799193d3b0359f4e9b6cedd2297d` (`git-sha`)
- Note: Repository compiler contract; the strictest of the two CLAUDE.md files.

### `role-personas`

- Target: `tackle.prompts`
- Kind: `db-table`
- Pinned revision: `persona-set@seed-2026-09` (`named-revision`)
- Note: Role persona sources. Per-role variation is hashed separately as system_prompt_hash, so this pin names the SET, not each persona.

### `procedure-registry`

- Target: `tackle.role_memory`
- Kind: `db-table`
- Pinned revision: `registry@2026-09` (`named-revision`)
- Note: Procedure-card registry, PG->Redis synced. Active cards are hashed per-walk into the snapshot's card set, so this pin names the registry itself.

### `conduit-state`

- Target: `conduit-mcp:3100`
- Kind: `service`
- Pinned revision: `live-state` (`live-locator`)
- Note: Pipeline authority. Deliberately NOT content-addressed: pinning live pipeline state would mint a new frame on every plan transition and fragment every cohort. Named as a live locator instead.

## Change discipline

Any change to the set of pinned texts, to their revisions, or to the ratifying decisions above is a **doctrine change**. It is reviewed as one, and it re-mints the bootstrap hash — deliberately, so that B1/B3 cohort queries segment by frame instead of silently mixing a governed run with an ungoverned one.

Edits to the pinned texts themselves do **not** move the frame unless the pins are deliberately updated. That separation is the point: a routine fix to a doctrine file must not retroactively re-frame executions that ran under the previous ratification.

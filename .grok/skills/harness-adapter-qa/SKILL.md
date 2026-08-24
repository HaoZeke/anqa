---
name: harness-adapter-qa
description: >
  Gate for adding or re-verifying a groket host harness adapter (Grok,
  OpenCode, Pi, later Claude/Codex/…). Use when adding a harness, when
  a product version changes, or when the user asks to QA / certify a
  store. Enforces the adapter contract, fixtures, version pin, and
  docs. Slash: /harness-adapter-qa
metadata:
  short-description: "Harness adapter completeness + version QA"
---

# Harness adapter QA

Groket lists native coding-agent stores through `groket/harness/`.
This skill is the gate before a new id ships and when a shipped
product version moves.

Contract: `docs/harness-adapters.md`. Interface: `groket/harness/types.py`.

## When a new adapter is the work

Do the **whole** adapter in one commit (or one tight stack that you
could revert alone). Do not land discover without timeline, or catalog
without query/`harness:<id>`.

### Must ship together

1. `groket/harness/<id>.py` implementing `HarnessAdapter`:
   `id`, `product`, `supported_version` (the CLI/app version you
   actually ran), `default_host_roots`, `discover`, `looks_like`,
   `load_meta` (sets `harness`, and `harness_version` when the store
   has a product version), `parse_timeline`, `ref_for_id`, `watch_hints`.
2. Register in `groket/harness/registry.py` `adapters()`.
3. Add the id to `HARNESS_IDS` in `groket/harness/ref.py` if new.
4. Add the id to the `harness` query token in
   `groket/integrations/control_contract.py`.
5. Add the id to HUD `is_harness_ref` in `desktop/src/live.rs`.
6. Default `[harness].host` in `groket/config.py` if the store should
   scan on a stock install.
7. `tests/session/test_harness_<id>.py` with **invented** fixtures
   (never copy real session text). Cover discover, meta, timeline
   tools, and child/subagent behaviour when the store has it.
8. `docs/harness-adapters.md` table row + README host sentence.
9. `just schema` if the control or config schema changed.

### Probe the live store first

On this machine, find the default root, list a real session, and write
the adapter from **observed keys**. Disk ≠ live child stdout. Do not
reuse Automedon `parse_line` on disk files.

Record `supported_version` from `opencode --version` / `pi --version` /
`grok --version` (or the product’s own about string). If the session
row also stores a version, map that to `SessionMeta.harness_version`.

### Run the gate

```bash
just lint                          # includes scripts/check_harness_adapters.py
uv run pytest tests/session/test_harness_<id>.py tests/session/test_harness_contract.py tests/session/test_query.py -q
just schema-check                  # if you touched contract/config schema
```

Fix gaps before the next adapter.

## When a shipped product version moved

1. Install/run the new CLI. Note the version string.
2. Re-read one real session (keys only in notes; no secret/session text
   in fixtures).
3. If the disk shape is the same, bump `supported_version` on the
   adapter and the docs table. Add a fixture row only for new keys
   you now parse.
4. If the shape changed, extend `parse_timeline` / `load_meta` in the
   **same** commit as the version bump. Keep one path; do not add a
   fallback parser “just in case.”
5. Re-run the gate above.

## Do not

- Ship an adapter that only lists sessions.
- Write notes or export into a foreign sqlite tree.
- Mark follow-up / Done / fork / rewind as present without a real
  product equivalent.
- Copy operator prompts or home paths into tests.
- Register an id that `check_harness_adapters.py` cannot prove.

---
name: harness-adapter-qa
description: >
  Gate for adding or re-verifying a anqa host harness adapter. Use
  when adding a harness, when a product version changes, or when the
  user asks to QA / certify a store. Enforces the adapter contract,
  fixtures, version pin, and docs. Slash: /harness-adapter-qa
metadata:
  short-description: "Harness adapter completeness + version QA"
---

# Harness adapter QA

Anqa lists native coding-agent stores through `anqa/harness/`.
This skill is the gate before a new id ships and when a shipped
product version moves.

Contract: `docs/harness-adapters.md`. Interface: `anqa/harness/types.py`.

## When a new adapter is the work

Do the **whole** adapter in one commit (or one tight stack that you
could revert alone). Do not land discover without timeline, or catalog
without query/`harness:<id>`.

### Must ship together

1. `anqa/harness/<id>.py` implementing `HarnessAdapter`:
   `id`, `product`, `supported_version` (the CLI/app version you
   actually ran), `default_host_roots`, `discover`, `looks_like`,
   `load_meta` (sets `harness`, and `harness_version` when the store
   has a product version), `parse_timeline`, `bind_locator`,
   `ref_for_id`, `watch_hints`, `write_archive`, `open_archive`,
   `load_detail`,
   `timeline_stamp`, `trace_mtime`, `updates_size`, `scheduler_state`,
   `reported_completion_ids`, `list_turn_outcome`.
2. Register in `anqa/harness/registry.py` `adapters()`.
3. Add the id to `HARNESS_IDS` in `anqa/harness/ref.py` if new.
4. Add the id to the `harness` query token in
   `anqa/control/contract.py`.
5. Add the id to HUD `is_harness_ref` in `desktop/src/live.rs`.
6. `tests/session/test_harness_<id>.py` driven from a committed
   synthesized store fixture (`tests/fixtures/harness/<id>/`, never
   copy real session text, never `db_path=` / mocks). Cover discover,
   meta, timeline tools, child/subagent behaviour when the store has
   it, **`list_session_catalog`** (the home list), **list status**
   (`list_status_label` running vs complete from that store’s own
   signals), and **`export_session_bundle`** through `require_adapter`
   / `Path("harness:id")` (the browser passes that path). Never default
   `turn_outcome` to success/complete. File and database locators
   must appear via `list_session_catalog` / `discover`. The
   session-directory walk only collects directory locators.
7. `docs/harness-adapters.md` table row + README host sentence.
8. `just schema` if the control or config schema changed.

### Probe the live store first

On this machine, find the default root, list a real session, and write
the adapter from **observed keys**. Disk ≠ live child stdout. Do not
reuse Automedon `parse_line` on disk files.

Record `supported_version` from the product CLI (`grok --version`, or
that store’s own about string). If the session row also stores a
version, map that to `SessionMeta.harness_version`.

### Run the gate

```bash
just lint                          # includes scripts/check_harness_adapters.py
uv run pytest tests/session/test_harness_<id>.py tests/session/test_harness_contract.py tests/session/test_query.py -q
just schema-check                  # if you touched contract/config schema
```

Fix gaps before the next adapter.

## When a shipped product version moved

1. Install/run the new CLI. Note the version string.
2. Run `just harness-probe`. It prints installed vs `supported_version`
   and on-disk record types (no session text).
3. Re-read the **published** parser or grammar for that product
   (`docs/harness-adapters.md` “Keeping adapters current”) **and** one
   real session (keys only in notes; no secret/session text in fixtures).
   Disk keys and the published grammar must both match what we parse.
4. If the disk shape is the same, bump `supported_version` on the
   adapter and the docs table. Add a fixture row only for new keys
   you now parse.
5. If the shape changed, extend `parse_timeline` / `load_meta` in the
   **same** commit as the version bump. Keep one path; do not add a
   fallback parser “just in case.”
6. Re-run the gate above.

## Do not

- Ship an adapter that only lists sessions.
- Write notes or export into a foreign sqlite tree.
- Mark follow-up / Done / fork / rewind as present without a real
  product equivalent.
- Copy operator prompts or home paths into tests.
- Register an id that `check_harness_adapters.py` cannot prove.
- Add an allowlist so a new store is hidden until the operator opts in.
  The catalog lists every shipped adapter; `[catalog] ignore` is opt-out.

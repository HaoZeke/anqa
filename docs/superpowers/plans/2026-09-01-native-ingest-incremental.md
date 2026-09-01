# Native ingest: incremental scan, cheap list, compact overview

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open and live-refresh any large session, on any shipped harness, without replaying the whole store into a Python event list.

**Architecture:** One rust `Store` per harness. Process cache is `Arc<[Event]>` keyed by harness + locator + session + *per-session* stamp, LRU-bounded. Append-only jsonl stores share one byte cursor. Gemini keeps a conversation-state cursor (`$set` / `$rewindTo`). SQLite stores stamp and resume *that session* (`seq` or per-id `events.jsonl` / transcript). `session/overview` asks rust for turn/stat rows and never calls `parse_timeline`. Adapters stay thin: `parse_timeline` and (once rust list-meta matches) `load_meta` call `anqa.core`. Delete unused Python `_timeline_for` / `_timeline_from_payload` / unused Grok incremental scanner after rust matches contract tests.

**Tech Stack:** Rust `anqa-core` (`anqa._core`), PyO3, rusqlite, pytest, `just core-check`, fixtures under `tests/fixtures/harness/{antigravity,claude,codex,copilot,cursor,gemini,opencode,pi}/` plus Grok directory fixtures.

---

# Problem

All nine adapters already call `anqa.core.timeline_events` for `parse_timeline`. The tax is the same: full native ingest on a stamp miss, unbounded `Vec` clone, PyO3 dicts, then `event_from_native` `json.loads(raw)`. `SessionOverview.uncached` always `parse_timeline`s that list. `session/timeline` is paged but a miss still ingests everything before slicing.

Do **not** restore `grok_parse.parse_timeline` or any adapter `_timeline_for` as a product path.

Assume the uncommitted native-page / `EventType` skip / `Store::{records,events}` work is on the branch before this plan starts.

## Store inventory

| Id | On-disk shape | List-meta today | Native `list_meta` | Stamp today | Incremental today | Dead / drifted Python |
|----|---------------|-----------------|--------------------|-------------|-------------------|------------------------|
| **antigravity** | `conversations/<id>.db` + `brain/<id>/…/transcript.jsonl` | Summary db + `JsonlFile.window` on transcript | Default stub | Newer of db file or transcript file | Full transcript read | `_timeline_for` unused by `parse_timeline` |
| **claude** | `~/.claude/projects/**/<uuid>.jsonl` | `JsonlFile.window` | Stub | That jsonl file | Full file | `_timeline_for` unused by `parse_timeline`; chrome types must stay off the timeline |
| **codex** | `rollout-*.jsonl` | `JsonlFile.window`; `num_events=len(_timeline_for(window))` | Stub | That jsonl file | Full file | `_timeline_for` used only to count the window |
| **copilot** | `session-store.db` + `session-state/<id>/events.jsonl` | Session **row** + `JsonlFile.window` on that id’s jsonl | Stub | Default `file_stamp(locator)` = **whole db** | Full events jsonl | `_timeline_for` used to count the window |
| **cursor** | `agent-transcripts/**/<id>.jsonl` + chat `meta.json` | Window + meta.json | Stub | That jsonl file | Full file | `_timeline_for` used to count the window |
| **gemini** | `session-*.jsonl` (`$set`, `$rewindTo`, `message_update`) | Window then reconstruct | Stub | That jsonl file | Full reconstruct from byte 0 | `_timeline_for` unused by `parse_timeline` |
| **grok** | Session dir `updates.jsonl` + `events.jsonl` | Cheap: summary / signals / 64 KiB tail | Stub | `updates.jsonl` only | Full file from byte 0; `lines()` allocates skipped 100MB shell lines; `windows()` needle; JSON twice; all `events.jsonl` types parsed | `_UpdatesScanState` unused |
| **opencode** | `opencode.db` (`event` 1.18 and/or `session`/`message`/`part`) | **Full** `_load_payload` replay | Stub | **Whole db file** | `SELECT … ORDER BY seq` from 0 | `_timeline_from_payload` maps `reasoning`; native event-table path does not. Tokens/cost selected, never mapped |
| **pi** | `**/*.jsonl` | `JsonlFile` header + tail | Rust `jsonl::window` (partial) | That jsonl file | Full file | `_timeline_for` unused by `parse_timeline` |

Shared overview tax: every harness, every `session/overview`.

Shared cache tax: process `HashMap`, no eviction, `timeline()` clones the whole `Vec<Event>`.

Product extras that stay unset (store does not write them): OpenCode context-meter / tasks / workflows / goals / plan files; other stores keep their existing extras (`signals.json` on Grok, rewind on Grok, `summary.diffs` on OpenCode).

---

# Files

| File | Role |
|------|------|
| `core/src/lib.rs` | `Arc<[Event]>` LRU cache; per-session stamp; `timeline` / `timeline_page` / `store_overview` / `store_stamp(session_id)` |
| `core/src/store.rs` | `stamp(locator, session_id)`; default `timeline`; optional overview |
| `core/src/overview.rs` | Compact turn/stat/job-bookend walk over `&[Event]` |
| `core/src/scan.rs` | `memchr` needle |
| `core/src/jsonl.rs` | Byte line walk; shared `JsonlCursor` (byte_pos + events) |
| `core/src/stores/grok.rs` | Two-file cursor; `events.jsonl` marker filter |
| `core/src/stores/pi.rs` | Cursor via shared jsonl helper; keep/improve `list_meta` |
| `core/src/stores/claude.rs` | Cursor + rust `list_meta` from window; skip chrome types |
| `core/src/stores/codex.rs` | Cursor + rust `list_meta` |
| `core/src/stores/cursor.rs` | Cursor + rust `list_meta` |
| `core/src/stores/gemini.rs` | Conversation-state cursor |
| `core/src/stores/copilot.rs` | Stamp = that session’s `events.jsonl`; cursor on that file |
| `core/src/stores/antigravity.rs` | Stamp = that session’s transcript (+ db extras); cursor on transcript |
| `core/src/stores/opencode.rs` | Cheap `list_meta`; `seq` cursor; one `map_part`; `records`/`events` |
| `anqa/core.py`, `anqa/_core.pyi` | `native_overview`, `list_meta`, stamp with session id |
| `anqa/session/control_views.py` | Overview without `parse_timeline` |
| `anqa/harness/*.py` | Flip `load_meta` / `timeline_stamp` to rust when that store’s rust `list_meta` matches; delete dead `_timeline_for` / `_timeline_from_payload` |
| `tests/session/test_harness_*.py`, `test_control_views.py`, `test_harness_contract.py` | Owning tests |
| `core/src/stores/*.rs` `#[cfg(test)]` | Incremental / cheap-meta / part-map tests |

---

### Task 1: Bounded `Arc` timeline cache, per-session stamp

**Files:**
- Modify: `core/src/lib.rs`, `core/src/store.rs`, every `core/src/stores/*.rs` `fn stamp`
- Test: `core/src/lib.rs`

- [ ] **Step 1: Write the failing rust test**

```rust
#[test]
fn timeline_cache_is_arc_and_bounded() {
    // Drive crate::timeline / crate::timeline_page on three tiny jsonl
    // sessions (pi or claude fixture shape in temp dirs).
    // Cap = 2. Ingest A, B. Page A is a hit. Ingest C. Next A rereads.
}
```

- [ ] **Step 2: Run — expect fail (no eviction or page hit still clones all events)**

```bash
cargo test --manifest-path core/Cargo.toml --lib timeline_cache_is_arc_and_bounded
```

- [ ] **Step 3: Implement**

```rust
struct TimelineEntry {
    stamp: FileStamp,
    events: Arc<[Event]>,
}
```

- `Store::stamp(&self, locator: &Path, session_id: &str) -> FileStamp`
- File jsonl stores: `jsonl::file_stamp(locator)` (already per session).
- Grok: `updates.jsonl` size/mtime in slots 0–1, `events.jsonl` size/mtime in 2–3.
- OpenCode: that session’s `MAX(seq)` or max message/part time — **not** the db file (Task 6 if not ready; Task 1 can pass `session_id` through and keep file stamp until Task 6).
- Copilot: `session-state/<id>/events.jsonl` stamp, not `session-store.db`.
- Antigravity: that session’s transcript stamp (and conversation db extras).
- Cache cap 32, LRU. `timeline_page` on hit slices `&[Event]` only.

- [ ] **Step 4: `cargo test --manifest-path core/Cargo.toml --lib`**

- [ ] **Step 5: Commit**

```bash
git commit -S -m "Bound native timeline cache with per-session stamps"
```

---

### Task 2: Compact native overview — every harness

**Files:**
- Create: `core/src/overview.rs`
- Modify: `core/src/lib.rs`, `anqa/core.py`, `anqa/_core.pyi`, `anqa/session/control_views.py`
- Test: `tests/session/test_control_views.py` plus one overview assertion per harness fixture in `tests/session/test_harness_contract.py` or existing `test_harness_*.py`

- [ ] **Step 1: Failing test (Grok fixture is enough for the boom; contract covers others)**

```python
def test_overview_does_not_parse_full_timeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sd = _write_session(tmp_path, "sess-ov-page")

    def boom(self: object, ref: object) -> list[object]:
        raise AssertionError("parse_timeline must not run for session/overview")

    monkeypatch.setattr("anqa.harness.grok.GrokAdapter.parse_timeline", boom)
    ov = build_session_overview(sd)
    assert ov["turns"]["total"] >= 1
    assert ov["timeline"]["lazy"] is True
```

Add the same boom against `PiAdapter.parse_timeline` / `ClaudeAdapter.parse_timeline` on their fixtures if those tests already open overview.

- [ ] **Step 2: Run — expect fail**

```bash
uv run pytest tests/session/test_control_views.py::test_overview_does_not_parse_full_timeline -q
```

- [ ] **Step 3: Implement**

Rust walk of cached `&[Event]` → `num_events`, turns (number, indexes, outcome, previews ≤400, tool/error counts), event-type counts, tool counts, subagent count, `task_backgrounded` / `task_completed` bookends.

`SessionOverview.uncached` uses `native_overview(adapter.id, locator, sid)`. No `parse_timeline`. Notes stay Python. Jobs that need only those two bookends come from the compact payload.

Existing overview tests (stats, jobs, one-shot) keep their assertions.

- [ ] **Step 4: Run**

```bash
uv run pytest tests/session/test_control_views.py tests/session/test_harness_contract.py tests/hud/test_control_wire.py -q
just core-check
```

Rebuild `anqa._core` before pytest.

- [ ] **Step 5: Commit**

```bash
git commit -S -m "Build session overview from a native turn and stat walk"
```

---

### Task 3: Shared jsonl byte cursor (seven file/transcript stores)

Applies to **pi, claude, codex, cursor, copilot events.jsonl, antigravity transcript, grok updates.jsonl**.

**Files:**
- Modify: `core/src/jsonl.rs`, `core/src/scan.rs`, those seven stores’ `records`/`events`
- Test: `core/src/jsonl.rs`, one store test each or one generic test with a temp jsonl

- [ ] **Step 1: Failing tests**

```rust
#[test]
fn jsonl_cursor_appends_without_reread() { /* write 1 line, ingest, append 1 line, count +1; truncate → no stale tail */ }

#[test]
fn jsonl_skip_line_without_string() {
    let mut line = br#"{"x":""#.to_vec();
    line.extend(std::iter::repeat(b'x').take(2 * 1024 * 1024));
    // helper returns None / Skip without from_utf8
}
```

- [ ] **Step 2: Run — expect fail**

```bash
cargo test --manifest-path core/Cargo.toml --lib jsonl_cursor_appends_without_reread
```

- [ ] **Step 3: Implement**

```rust
pub struct JsonlCursor {
    pub byte_pos: u64,
    pub records: Vec<Record>,
}
impl JsonlCursor {
    pub fn sync(&mut self, path: &Path) { /* size < pos → reset; size > pos → seek and extend */ }
}
```

- Split file on `\n` as bytes. Optional keep-fn (`keep_updates_line` for Grok) runs on `&[u8]` **before** `String`.
- Replace `windows()` with `memchr::memmem::find`.
- Each of the seven stores’ `records()` uses `JsonlCursor` (Grok updates half here; events.jsonl in Task 4).
- `consume` / `events()` must use already-parsed `Record.value` (no second `object_line` on `raw`).

- [ ] **Step 4: Run**

```bash
cargo test --manifest-path core/Cargo.toml --lib
uv run pytest tests/session/test_harness_pi.py tests/session/test_harness_claude.py tests/session/test_harness_codex.py tests/session/test_harness_cursor.py tests/session/test_harness_copilot.py tests/session/test_harness_antigravity.py -q
```

- [ ] **Step 5: Commit**

```bash
git commit -S -m "Resume append-only jsonl stores from the last byte"
```

---

### Task 4: Grok two-file cursor and marker filter

**Files:** `core/src/stores/grok.rs`  
**Test:** `core/src/stores/grok.rs` (keep `runtime_noise_is_not_a_timeline_row`)

- [ ] **Step 1: Failing test** — append to `updates.jsonl` only; `events.jsonl` unchanged; new user row appears; truncate updates rebuilds. Fat non-terminal `tool_call_update` does not appear in `records()`.

- [ ] **Step 2: Run — expect fail until cursor + skip land**

```bash
cargo test --manifest-path core/Cargo.toml --lib grok
```

- [ ] **Step 3: Implement**

- Updates half: Task 3 cursor + `keep_updates_line`.
- `events.jsonl`: keep a line only if it contains the same needles as `_LIST_MARKER_NEEDLES` in `anqa/harness/grok_parse.py` (`turn_started`, `turn_ended`, `session_error`, `"type":"error"`, `turn_error`, `fatal_error`). Then map only `EventType::is_turn_marker()`.
- Stamp already two-file from Task 1.

- [ ] **Step 4: `cargo test --manifest-path core/Cargo.toml --lib grok` and `uv run pytest tests/session/test_harness_grok.py tests/session/test_control_views.py -q`**

- [ ] **Step 5: Commit**

```bash
git commit -S -m "Resume Grok updates from the last byte and skip runtime rows"
```

---

### Task 5: Gemini conversation-state cursor

Gemini is not append-only events. Lines are `$set`, `$rewindTo`, `message_update`, and new messages. A byte cursor must **apply new patches** to the in-memory conversation, not concat raw records.

**Files:** `core/src/stores/gemini.rs`  
**Test:** `core/src/stores/gemini.rs`, `tests/session/test_harness_gemini.py`

- [ ] **Step 1: Failing test**

```rust
#[test]
fn gemini_append_message_updates_conversation() {
    // Write a session-*.jsonl with header + one user + one gemini.
    // timeline() once. Append a user line. timeline() has a new user row.
    // Append $rewindTo clearing messages. timeline() has no user rows.
}
```

- [ ] **Step 2: Run — expect fail (full `load_conversation` already passes append; `$rewindTo` after a cached full vec is the fail if you only concat)**

Implement a `GeminiCursor { byte_pos, metadata, messages, order }` that `load_conversation` already almost is. On append, seek and apply new lines to that state. On `$rewindTo` / size shrink, reset.

- [ ] **Step 3: Implement** — fold `load_conversation` into the cursor; `records()` emits reconstructed messages as today; `events()` stays `timeline_of`.

- [ ] **Step 4: `cargo test --manifest-path core/Cargo.toml --lib gemini` and `uv run pytest tests/session/test_harness_gemini.py -q`**

- [ ] **Step 5: Commit**

```bash
git commit -S -m "Apply Gemini jsonl patches from a conversation cursor"
```

---

### Task 6: OpenCode cheap list-meta and token fields

**Files:** `core/src/stores/opencode.rs`, `anqa/harness/opencode.py`, `anqa/models.py` if cost needs a field  
**Test:** `tests/session/test_harness_opencode.py`

- [ ] **Step 1: Failing test** — `monkeypatch` `_load_payload` to boom; `OpenCodeAdapter.load_meta(fixture_ref)` must succeed; `title` / `turn_outcome` set; `context_tokens_used` reflects `tokens_input+output+reasoning` when the fixture or a temp row has them.

- [ ] **Step 2: Run — expect fail**

```bash
uv run pytest tests/session/test_harness_opencode.py::test_list_meta_does_not_replay_event_table -q
```

- [ ] **Step 3: Implement** rust `list_meta`: `session` row first; live 1.18 last `session.updated` + last part `state.status` (`ORDER BY seq DESC LIMIT 1`). Adapter `load_meta` / `list_turn_outcome` → `anqa.core.list_meta`. `_load_payload` remains for archive / `file_diffs_for`.

- [ ] **Step 4: `uv run pytest tests/session/test_harness_opencode.py tests/session/test_harness_contract.py -q`**

- [ ] **Step 5: Commit**

```bash
git commit -S -m "Fill OpenCode list meta from the session row"
```

---

### Task 7: OpenCode `seq` resume and one `records`/`events` path

**Files:** `core/src/stores/opencode.rs`, `anqa/harness/opencode.py` (`timeline_stamp`)  
**Test:** `core/src/stores/opencode.rs`

- [ ] **Step 1: Failing tests** — `opencode_stamp_is_per_session` (write to B, stamp A unchanged); `opencode_timeline_resumes_after_last_seq`.

- [ ] **Step 2: Run — expect fail (stamp is still the db file)**

- [ ] **Step 3: Implement** — `stamp` = `MAX(seq)` for that `aggregate_id` (or max message/part time). Cache `last_seq` + events. `SELECT … AND seq > last_seq`. Lower max seq → full replay. Delete empty `records`/`events` stubs; `timeline()` uses the trait default.

- [ ] **Step 4: `cargo test --manifest-path core/Cargo.toml --lib opencode` and `uv run pytest tests/session/test_harness_opencode.py -q`**

- [ ] **Step 5: Commit**

```bash
git commit -S -m "Stamp and resume OpenCode timelines per session seq"
```

---

### Task 8: Copilot and Antigravity per-session stamp + jsonl cursor

**Files:** `core/src/stores/copilot.rs`, `core/src/stores/antigravity.rs`, adapters’ `timeline_stamp`  
**Test:** rust unit tests in those modules; `tests/session/test_harness_copilot.py`, `tests/session/test_harness_antigravity.py`

- [ ] **Step 1: Failing tests**

```rust
#[test]
fn copilot_stamp_follows_that_session_events_file() { /* two sids; grow B’s events.jsonl; stamp(A) unchanged */ }

#[test]
fn antigravity_stamp_follows_that_session_transcript() { /* same idea on transcript.jsonl */ }
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement** — `stamp(locator, session_id)` points at `session-state/<id>/events.jsonl` / `transcript_path(root, sid)`. `records()` uses Task 3 `JsonlCursor` on that file. Adapter `timeline_stamp` calls `store_stamp` with `session_id`.

- [ ] **Step 4: Owning pytest + `cargo test --lib copilot antigravity`**

- [ ] **Step 5: Commit**

```bash
git commit -S -m "Stamp Copilot and Antigravity timelines per session file"
```

---

### Task 9: Rust `list_meta` for every file store; stop counting via `_timeline_for`

**Files:** `core/src/stores/{pi,claude,codex,cursor,gemini,copilot,antigravity,grok}.rs`; `anqa/harness/{codex,cursor,copilot}.py` (`num_events=len(_timeline_for(window))`)  
**Test:** existing `test_harness_*.py` list-meta tests; boom `_timeline_for` in catalog/list tests if any

- [ ] **Step 1: Failing tests** — for Codex/Cursor/Copilot, `load_meta` on the fixture with `_timeline_for` patched to boom must still return title and turn_outcome. Rust `list_meta` tests: window title / last-turn token match the fixture.

- [ ] **Step 2: Run — expect fail on Codex/Cursor/Copilot boom**

- [ ] **Step 3: Implement** rust `list_meta` from `jsonl::window` (and Copilot session row + events window; Antigravity summary + transcript window; Grok keep Python cheap list — `summary.json` / `signals.json` / tail — or port that tail to rust in this task). Flip those adapters’ `load_meta` to `anqa.core.list_meta` when the rust result matches `tests/session/test_harness_*.py`. Replace `num_events=len(_timeline_for(window))` with `0` or a cheap window count, not a full mapper.

Claude chrome types (`progress`, `file-history-snapshot`, …) stay out of rust `events()` (already skipped if rust only maps `user`/`assistant`).

- [ ] **Step 4: `uv run pytest tests/session/test_harness_pi.py tests/session/test_harness_claude.py tests/session/test_harness_codex.py tests/session/test_harness_cursor.py tests/session/test_harness_gemini.py tests/session/test_harness_copilot.py tests/session/test_harness_antigravity.py tests/session/test_harness_grok.py tests/session/test_harness_contract.py -q`**

- [ ] **Step 5: Commit**

```bash
git commit -S -m "Fill every store list meta from a rust window"
```

---

### Task 10: One mapper per store; delete dead Python timeline builders

**Files:** each `core/src/stores/*.rs`; delete unused functions in `anqa/harness/{pi,claude,codex,cursor,gemini,copilot,antigravity,opencode}.py`  
**Test:** `tests/session/test_harness_contract.py` + each `test_harness_*.py`

Locked OpenCode part map (Task 7 mapper must use this):

| OpenCode `part.type` | `EventType` |
|----------------------|-------------|
| `text` user | `UserMessageChunk` |
| `text` assistant | `AgentMessageChunk` |
| `reasoning` | `AgentThoughtChunk` |
| `tool` | `ToolCall` + `ToolCallUpdate`; `task` also spawn/finish |
| `compaction` | `CompactionCheckpoint` |
| `permission` | `System` |
| `file`, `patch` | `ToolCall` |
| `snapshot`, `step` | `System` |

Other stores — rust `events()` must keep contract fixtures green. Known gaps to close in this task:

- OpenCode event-table path: add `reasoning` (Python `_timeline_from_payload` already has it).
- Pi: last-assistant text on subagent finish (`details.results`) — rust already has a path; confirm fixture.
- Claude: Agent/Task spawn bookends from `toolUseResult.agentId`.
- Gemini: thoughts via `description`; tool `result` → `tool_call_update`.
- Copilot: `agentId` on finish rows.

- [ ] **Step 1: Failing rust test** `opencode_event_table_maps_reasoning_to_thought` (and any fixture assertion that is already red).

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement `map_part` / fill gaps. `rg '_timeline_for|_timeline_from_payload' anqa/harness` — delete functions with no remaining callers.**

- [ ] **Step 4: `just core-check` and `uv run pytest tests/session/test_harness_contract.py tests/session/test_harness_*.py -q`**

- [ ] **Step 5: Commit**

```bash
git commit -S -m "Map remaining native parts and delete unused Python timeline builders"
```

---

### Task 11: Prove every fixture and one large live session per shape

- [ ] **Step 1: Rebuild `_core` and restart `anqad`**

```bash
cargo build --release --manifest-path core/Cargo.toml --features extension-module --locked
cp -f target/release/lib_core.so anqa/_core.cpython-313-x86_64-linux-gnu.so
ANQA_SERVE_LOG_LEVEL=DEBUG anqad restart
```

- [ ] **Step 2: Fixture suite (all nine)**

```bash
uv run pytest tests/session/test_harness_contract.py tests/session/test_harness_antigravity.py tests/session/test_harness_claude.py tests/session/test_harness_codex.py tests/session/test_harness_copilot.py tests/session/test_harness_cursor.py tests/session/test_harness_gemini.py tests/session/test_harness_grok.py tests/session/test_harness_opencode.py tests/session/test_harness_pi.py tests/session/test_control_views.py -q
```

- [ ] **Step 3: Live timings**

Grok directory (append-only jsonl + markers):

```bash
uv run python - <<'PY'
import time
from pathlib import Path
from anqa.session.control_views import build_session_overview, build_session_timeline
loc = Path.home() / ".grok/sessions/%2Fmnt%2Fdev%2F_git%2Fanqa/01a04972-5b44-7510-a176-97967810539f"
t0 = time.perf_counter(); ov = build_session_overview(loc)
print("grok overview", f"{(time.perf_counter()-t0)*1000:.0f}ms", "turns", ov["turns"]["total"])
t0 = time.perf_counter(); tl = build_session_timeline(loc, offset=0, limit=200)
print("grok page", f"{(time.perf_counter()-t0)*1000:.0f}ms", "total", tl["total"])
t0 = time.perf_counter(); build_session_timeline(loc, offset=0, limit=200)
print("grok page-warm", f"{(time.perf_counter()-t0)*1000:.0f}ms")
PY
```

Pass: warm page &lt; 50ms; cold overview &lt; 500ms after Tasks 2+4; append one `updates.jsonl` line does not return to multi-second ingest.

If present, time the largest session in:

- `~/.local/share/opencode/opencode.db` (sqlite + seq)
- `~/.claude/projects` (jsonl cursor)
- `~/.codex/sessions` (jsonl cursor)
- `~/.gemini/tmp` (Gemini cursor)
- `~/.copilot/session-state` (per-id jsonl)
- `~/.pi/agent/sessions` (jsonl)
- `~/.cursor/projects` (jsonl)
- `~/.gemini/antigravity-cli` (transcript)

WAL / sibling-session write must not change another session’s rust stamp (OpenCode, Copilot db).

- [ ] **Step 4: Commit only if Step 3 needed a code fix**

---

# Order

```
Task 1  cache + stamp(session_id)
  ├─ Task 2  compact overview (all harnesses)
  ├─ Task 3  shared jsonl cursor (pi, claude, codex, cursor, copilot file, antigravity file, grok updates)
  │    ├─ Task 4  Grok two-file + markers
  │    └─ Task 8  Copilot / Antigravity per-session file stamp
  ├─ Task 5  Gemini conversation cursor
  └─ Task 6  OpenCode cheap list
       └─ Task 7  OpenCode seq + records/events
Task 9   rust list_meta for every store (after 3; OpenCode after 6)
Task 10  mapper parity + delete dead Python (after 7 for OpenCode)
Task 11  prove all nine
```

2 ∥ 3 after 1. 5 ∥ 6 after 1.

---

# Out of this plan

- Inventing OpenCode context-meter / tasks / workflows / goals / plan files.
- Restoring `grok_parse.parse_timeline` or adapter `_timeline_for` as ingest.
- A second cache beside `TIMELINE`.
- Catalog performance essays.

---

# Self-review

| Spec / store item | Task |
|-------------------|------|
| All nine `parse_timeline` → native | already true; 3–8 make it incremental |
| Overview never `parse_timeline` | 2 |
| Arc + LRU | 1 |
| Grok byte cursor, fat-line skip, marker filter | 3 + 4 |
| Pi / Claude / Codex / Cursor jsonl append | 3 |
| Gemini `$set` / `$rewindTo` | 5 |
| OpenCode cheap list + tokens | 6 |
| OpenCode `seq` + not whole-db stamp | 7 |
| Copilot / Antigravity not whole-db stamp | 8 |
| Rust list_meta; stop `_timeline_for` counts | 9 |
| Reasoning + remaining OpenCode parts; delete dead Python | 10 |
| Prove every fixture + live shapes | 11 |

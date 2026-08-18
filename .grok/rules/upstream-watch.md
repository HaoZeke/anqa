# Upstream watch (standing)

For every session in this repo, keep a live watch on two streams and
act when something changes. Do not wait for the operator to ask.

## 1. icedtea (HUD dependency)

- Pin lives in `desktop/Cargo.toml` (`icedtea = "…"`).
- Shared board: `vissue` project **icedtea**, tags `from-groket` /
  requests filed from here.
- When icedtea marks a groket-filed issue **DONE**, or crates.io /
  GitHub publishes a newer icedtea than the pin:
  1. Read the append / changelog.
  2. Bump the pin (crate index preferred when the version is published).
  3. Drop stand-ins or workarounds the fix covers.
  4. Update `CHANGELOG.md` / `TODO.md`, run HUD checks, commit.
- Run `scripts/watch-upstream.sh` (or the same checks by hand) when
  starting non-trivial work, and leave a durable schedule or session
  monitor running when you open this repo.

## 2. Incoming pull requests (`indynull/groket`)

- Watch open pull requests on `indynull/groket` (GitHub).
- On a new or updated PR: summarize intent, check continuous integration,
  and either review (house `my-review` when appropriate) or say what is
  blocked. Do not merge unless the operator explicitly says to merge.
- Dependabot and human PRs both count.

## 3. How to watch

- Prefer a **durable** scheduled task (`interval` about `1h` or `2h`)
  whose prompt runs `scripts/watch-upstream.sh` and only notifies on
  **change** (new icedtea version, new DONE vissue, new/updated PR).
- In an interactive session, also start a **monitor** or keep the
  schedule; do not drop the duty when the conversation moves on.
- State file (last seen): `~/.groket/upstream-watch-state.json`
  (machine-local; not committed).

## 4. Non-goals

- Editing the icedtea tree from groket sessions (file `vissue` there).
- Force-push or merge without explicit operator instruction.

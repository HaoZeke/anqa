#!/usr/bin/env bash
# Report icedtea pin drift, done icedtea fixes for groket, and open PRs.
# Exit 0 always. Prints CHANGE lines only when something new appears
# (vs ~/.groket/upstream-watch-state.json). Prints OK when quiet.
set -euo pipefail

REPO="${GROKET_REPO:-indynull/groket}"
STATE_DIR="${GROKET_HOME:-$HOME/.groket}"
STATE_FILE="${UPSTREAM_WATCH_STATE:-$STATE_DIR/upstream-watch-state.json}"
VISSUE_ROOT="${VISSUE_ROOT:-$HOME/_dev/issues}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

mkdir -p "$STATE_DIR"

pin=""
if [[ -f "$ROOT/desktop/Cargo.toml" ]]; then
  pin=$(sed -n 's/^icedtea = "\([^"]*\)"/\1/p' "$ROOT/desktop/Cargo.toml" | head -1)
fi

crates=""
if crates_line=$(curl -fsSL 'https://index.crates.io/ic/ed/icedtea' 2>/dev/null | tail -1); then
  crates=$(printf '%s' "$crates_line" | python3 -c 'import sys,json; print(json.loads(sys.stdin.read()).get("vers",""))' 2>/dev/null || true)
fi

pr_json="[]"
if command -v gh >/dev/null 2>&1; then
  pr_json=$(gh pr list --repo "$REPO" --state open --limit 30 \
    --json number,title,author,updatedAt,url 2>/dev/null || echo '[]')
fi

vissue_done=""
if command -v vissue >/dev/null 2>&1 && [[ -d "$VISSUE_ROOT" ]]; then
  # DONE icedtea issues (ids). New DONE lines are CHANGE.
  vissue_done=$(
    VISSUE_ROOT="$VISSUE_ROOT" VISSUE_AGENT=groket \
      vissue --root "$VISSUE_ROOT" list --project icedtea --state DONE 2>/dev/null \
      | awk '{print $1}' \
      || true
  )
fi

python3 - "$STATE_FILE" "$pin" "$crates" "$pr_json" "$vissue_done" <<'PY'
import json, sys
from pathlib import Path

state_path = Path(sys.argv[1])
pin, crates, pr_json, vissue_done = sys.argv[2:6]
prs = json.loads(pr_json or "[]")
pr_key = sorted(
    (int(p["number"]), p.get("updatedAt") or "", p.get("title") or "")
    for p in prs
)
done_lines = [ln.strip() for ln in vissue_done.splitlines() if ln.strip()]
cur = {
    "icedtea_pin": pin,
    "icedtea_crates": crates,
    "prs": [{"n": n, "u": u, "t": t} for n, u, t in pr_key],
    "vissue_done": done_lines,
}

prev = {}
if state_path.is_file():
    try:
        prev = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        prev = {}

def ver_tuple(s: str) -> tuple:
    parts = []
    for p in (s or "0").split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


changed = []
if pin and crates:
    if ver_tuple(crates) > ver_tuple(pin):
        if prev.get("icedtea_crates") != crates or prev.get("icedtea_pin") != pin:
            changed.append(f"CHANGE icedtea crates.io={crates} pin={pin}")

if done_lines != prev.get("vissue_done"):
    new = [x for x in done_lines if x not in set(prev.get("vissue_done") or [])]
    for ln in new or done_lines[:3]:
        changed.append(f"CHANGE vissue {ln}")

prev_prs = {(p["n"], p["u"]) for p in prev.get("prs") or []}
cur_prs = {(p["n"], p["u"]) for p in cur["prs"]}
if cur_prs != prev_prs:
    for p in prs:
        key = (int(p["number"]), p.get("updatedAt") or "")
        if key not in prev_prs:
            who = (p.get("author") or {}).get("login") or "?"
            changed.append(
                f"CHANGE pr #{p['number']} {p.get('title','')} (@{who}) {p.get('url','')}"
            )

state_path.write_text(json.dumps(cur, indent=2) + "\n", encoding="utf-8")

if changed:
    for line in changed:
        print(line)
else:
    print(f"OK icedtea pin={pin or '?'} crates={crates or '?'} open_prs={len(prs)}")
PY

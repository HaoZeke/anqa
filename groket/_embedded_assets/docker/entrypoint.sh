#!/bin/bash
set -e

REPO_URL="${REPO_URL:-}"
MODEL="${MODEL:-grok-build}"
REPO_BRANCH="${REPO_BRANCH:-}"
PROMPT="${PROMPT:-}"
SKIP_SETUP="${SKIP_SETUP:-0}"

# Host uid/gid (set by orchestrator) — sessions bind-mount is written as root inside
# the container; chown so the host user can delete/read prompt_history.jsonl etc.
_gte_fix_sessions_ownership() {
    local uid="${HOST_UID:-}"
    local gid="${HOST_GID:-}"
    if [ -z "$uid" ] || [ -z "$gid" ]; then
        return 0
    fi
    if [ ! -d /root/.grok/sessions ]; then
        return 0
    fi
    if chown -R "${uid}:${gid}" /root/.grok/sessions 2>/dev/null; then
        echo ">>> Sessions volume ownership → ${uid}:${gid} (host user)"
    fi
}
trap '_gte_fix_sessions_ownership' EXIT

# GitHub CLI in automation: accept host-injected token when job opts into github_write
# (orchestrator sets GH_TOKEN from GH_TOKEN on the host).
if [ -z "${GH_TOKEN:-}" ] && [ -n "${GITHUB_TOKEN:-}" ]; then
    export GH_TOKEN="$GITHUB_TOKEN"
fi
export GH_PAGER="${GH_PAGER:-cat}"
export GIT_TERMINAL_PROMPT="${GIT_TERMINAL_PROMPT:-0}"

# Wire git push/fetch to use GH_TOKEN via gh (plain `git` does not read GH_TOKEN alone).
_gte_setup_git_gh_auth() {
    if [ -z "${GH_TOKEN:-}" ]; then
        return 0
    fi
    if ! command -v gh >/dev/null 2>&1; then
        echo ">>> WARNING: GH_TOKEN set but gh not installed — git push may fail (use fully-loaded profile)."
        return 0
    fi
    if ! command -v git >/dev/null 2>&1; then
        echo ">>> WARNING: GH_TOKEN set but git not installed."
        return 0
    fi
    # Non-interactive: gh as git credential helper so `git push` works with the PAT.
    set +e
    gh auth setup-git >/dev/null 2>&1
    git config --global credential.helper '!gh auth git-credential' >/dev/null 2>&1
    # Sensible defaults for agent commits inside eval (override in setup.sh if needed).
    git config --global user.email "${GIT_AUTHOR_EMAIL:-groket-eval@localhost}" >/dev/null 2>&1
    git config --global user.name "${GIT_AUTHOR_NAME:-groket-eval}" >/dev/null 2>&1
    set -e
    echo ">>> git credential helper → gh (GH_TOKEN) — git push/fetch over HTTPS should work."
}

if [ -n "${GH_TOKEN:-}" ]; then
    echo ">>> GH_TOKEN present — gh/API auth available (non-interactive)."
    if [ "${GITHUB_WRITE:-0}" = "1" ]; then
        echo ">>> GITHUB_WRITE=1 — write/push enabled for this job (repo-scoped token recommended)."
    fi
    _gte_setup_git_gh_auth
else
    # Informational only — public HTTPS clones do not need a token.
    echo ">>> No GH_TOKEN (optional): private remotes / git push need a persona PAT or host GH_TOKEN."
fi

# Repo is optional — jobs may be "prompt + initial commands" only (empty workspace).
if [ -n "$REPO_URL" ] && [ ! -d /workspace/.git ]; then
    if ! command -v git >/dev/null 2>&1; then
        echo ">>> ERROR: git not installed in the image; cannot clone $REPO_URL"
        echo ">>> Use docker_image fully-loaded (or an image with git)."
        exit 127
    fi
    echo ">>> Cloning $REPO_URL${REPO_BRANCH:+ (branch $REPO_BRANCH)} ..."
    set +e
    if [ -n "$REPO_BRANCH" ]; then
        git clone --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" /workspace
        clone_rc=$?
    else
        git clone --depth 1 "$REPO_URL" /workspace
        clone_rc=$?
    fi
    set -e
    if [ "$clone_rc" -ne 0 ]; then
        echo ">>> ERROR: git clone failed (exit $clone_rc)"
        echo ">>>   url:    $REPO_URL"
        echo ">>>   branch: ${REPO_BRANCH:-"(default)"}"
        if [ -z "${GH_TOKEN:-}" ]; then
            echo ">>> Public HTTPS repos need network + git only."
            echo ">>> Private repos need GH_TOKEN (persona github_token / github_token_env, or host GH_TOKEN)."
        fi
        exit "$clone_rc"
    fi
    echo ">>> Clone complete."
elif [ -z "$REPO_URL" ]; then
    echo ">>> No REPO_URL — starting in empty /workspace (no-repo job)."
    mkdir -p /workspace
fi

cd /workspace

# Re-apply git↔gh wiring inside /workspace (local config can override globals in some images).
if [ -n "${GH_TOKEN:-}" ] && [ -d /workspace/.git ] && command -v git >/dev/null 2>&1; then
    set +e
    git config credential.helper '!gh auth git-credential' >/dev/null 2>&1
    set -e
fi

# Install persona/runner marketplace plugins from /groket-plugins-manifest.json.
# Single path: git clone (+ optional commit checkout), then
# ``grok plugin install --trust <local-dir>``. Requires git and grok.
# Do not pass url@sha to grok (suffix is treated as a branch name).
_gte_install_plugins_from_manifest() {
    local manifest="${1:-/groket-plugins-manifest.json}"
    if [ ! -f "$manifest" ]; then
        return 0
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        echo ">>> WARNING: plugins manifest present but python3 missing — skipping plugin install."
        return 0
    fi
    if ! command -v git >/dev/null 2>&1 || ! command -v grok >/dev/null 2>&1; then
        echo ">>> WARNING: plugins manifest present but git+grok required — skipping plugin install."
        return 0
    fi
    echo ">>> Installing Grok plugins from manifest ..."
    # shellcheck disable=SC2016
    python3 - "$manifest" <<'PY'
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

manifest_path = Path(sys.argv[1])
try:
    items = json.loads(manifest_path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f">>> WARNING: cannot read plugins manifest: {exc}", file=sys.stderr)
    sys.exit(0)
if not isinstance(items, list):
    sys.exit(0)

for item in items:
    if not isinstance(item, dict):
        continue
    name = str(item.get("name") or "").strip()
    url = str(item.get("source_url") or "").strip()
    sha = str(item.get("sha") or "").strip()
    if not name or not url:
        continue
    tmp = Path(tempfile.mkdtemp(prefix=f"groket-pl-{name}-"))
    src = tmp / "src"
    try:
        subprocess.run(
            ["git", "clone", "--quiet", url, str(src)],
            check=True,
            capture_output=True,
            timeout=300,
            text=True,
        )
        if sha:
            subprocess.run(
                ["git", "-C", str(src), "checkout", "--quiet", sha],
                check=True,
                capture_output=True,
                timeout=60,
                text=True,
            )
        r = subprocess.run(
            ["grok", "plugin", "install", "--trust", str(src)],
            capture_output=True,
            timeout=300,
            text=True,
        )
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if r.returncode != 0 or out.lower().startswith("error") or "install failed" in out.lower():
            print(
                f">>> WARNING: plugin {name} install failed: {out[:800] or r.returncode}",
                file=sys.stderr,
            )
        else:
            print(f">>> plugin {name} installed ({url})")
    except Exception as exc:
        err = getattr(exc, "stderr", None) or exc
        print(f">>> WARNING: plugin {name} install failed: {err}", file=sys.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
PY
}

_gte_install_plugins_from_manifest

# Multiline initial commands (setup.sh baked into the image). Runs after clone
# so commands can populate /workspace, install packages, write seed files, etc.
if [ "$SKIP_SETUP" != "1" ] && [ -x /groket-setup.sh ]; then
    if grep -qvE '^[[:space:]]*(#|$)' /groket-setup.sh 2>/dev/null; then
        echo ">>> Running initial commands (/groket-setup.sh) ..."
        # Don't abort the whole job if setup fails unless set -e inside the script.
        set +e
        /bin/bash /groket-setup.sh
        setup_rc=$?
        set -e
        if [ "$setup_rc" -ne 0 ]; then
            echo ">>> WARNING: initial commands exited with code $setup_rc (continuing to grok)"
        else
            echo ">>> Initial commands done."
        fi
    fi
fi

echo "============================================"
echo "  Model:  $MODEL"
if [ -n "$REPO_URL" ]; then
    echo "  Repo:   $REPO_URL${REPO_BRANCH:+ @ $REPO_BRANCH}"
else
    echo "  Repo:   (none — no-repo job)"
fi
if [ -n "${GH_TOKEN:-}" ]; then
    if [ "${GITHUB_WRITE:-0}" = "1" ]; then
        echo "  GH:     write token injected (git+gh; prefer GH_TOKEN on host)"
    else
        echo "  GH:     token injected (GH_TOKEN set)"
    fi
else
    echo "  GH:     no token (pivot / unauthenticated)"
fi
echo "  CWD:    $(pwd)"
echo "============================================"
echo ""

# Resolve prompt into a file. Passing `-p "$PROMPT"` breaks when:
#   - PROMPT is empty / whitespace-only  → "prompt is empty"
#   - PROMPT starts with `-` (e.g. `--help`) → argparse eats it ("value required for --single")
#   - PROMPT has newlines/special chars  → env truncation / shell issues
# Prefer a host-mounted /groket-prompt.txt (orchestrator writes this); fall back to PROMPT env.
PROMPT_FILE="/tmp/groket-prompt.txt"
if [ -f /groket-prompt.txt ] && [ -s /groket-prompt.txt ]; then
    cp /groket-prompt.txt "$PROMPT_FILE"
elif [ -n "$PROMPT" ]; then
    # Preserve exact bytes from env (may still lose newlines if docker stripped them)
    printf '%s' "$PROMPT" > "$PROMPT_FILE"
    # If env had no trailing newline but content exists, fine; trim only all-whitespace
fi

# Trim: treat all-whitespace as missing
if [ ! -s "$PROMPT_FILE" ] || ! grep -q '[^[:space:]]' "$PROMPT_FILE" 2>/dev/null; then
    echo ">>> ERROR: No prompt provided (empty /groket-prompt.txt and PROMPT env)."
    echo ">>> Set a prompt in the Runner / task YAML before launching."
    exit 2
fi

echo ">>> Prompt file: $PROMPT_FILE ($(wc -c < "$PROMPT_FILE") bytes)"
# Prompt is exactly what the operator set (Runner / task YAML) — no extra preamble.

# Newest session dir under bind-mounted traces (host sees the same path).
_gte_find_session() {
    find /root/.grok/sessions -mindepth 2 -maxdepth 2 -type d \
        ! -name 'compaction' 2>/dev/null | while read -r d; do
            if [ -f "$d/chat_history.jsonl" ] || [ -f "$d/updates.jsonl" ] || [ -f "$d/summary.json" ]; then
                echo "$d"
            fi
        done | sort | tail -1
}

# One share snapshot (force=1 refreshes even if a prior URL exists).
_gte_share_once() {
    local force_flag="${1:-}"
    local sess
    sess=$(_gte_find_session)
    if [ -z "$sess" ] || [ ! -d "$sess" ]; then
        echo ">>> [share] no session dir yet"
        return 1
    fi
    local sid
    sid=$(basename "$sess")
    echo ">>> [share] snapshot for $sid force=${force_flag:-0}"
    if [ -f /groket-share-once.py ]; then
        python3 /groket-share-once.py "$sid" "$sess" $force_flag || return 1
    else
        echo ">>> [share] /groket-share-once.py missing in image"
        return 1
    fi
}

# Background: periodic mid-run shares (default on ALL images; entrypoint always starts this).
# Final share runs after agent exits (below). Opt-out only via SHARE_DISABLE=1.
# Cadence tuned for less jitter / fewer overlapping ``grok share`` calls:
#   SHARE_INITIAL_DELAY_SECS (default 30) — wait for session + first turns
#   SHARE_INTERVAL_SECS (default 60) — min age before re-snapshot once URL exists
#   SHARE_LOOP_SLEEP_SECS (default 20) — idle poll between attempts
# First successful share uses non-force; subsequent snapshots force so the share page advances.
_gte_share_loop() {
    local interval="${SHARE_INTERVAL_SECS:-60}"
    local loop_sleep="${SHARE_LOOP_SLEEP_SECS:-20}"
    sleep "${SHARE_INITIAL_DELAY_SECS:-30}"
    local had_url=0
    while true; do
        local sess
        sess=$(_gte_find_session)
        if [ -z "$sess" ] || [ ! -d "$sess" ]; then
            sleep "$loop_sleep"
            continue
        fi
        if [ -f "$sess/groket-share.json" ] && grep -q '"share_url": "https' "$sess/groket-share.json" 2>/dev/null; then
            had_url=1
            local age=9999
            if command -v stat >/dev/null 2>&1; then
                age=$(( $(date +%s) - $(stat -c %Y "$sess/groket-share.json" 2>/dev/null || echo 0) ))
            fi
            if [ "$age" -lt "$interval" ]; then
                sleep "$loop_sleep"
                continue
            fi
        fi
        # Force only when refreshing an existing share (reduces first-snapshot races).
        local force_flag=""
        if [ "$had_url" -eq 1 ]; then
            force_flag="1"
        fi
        _gte_share_once "$force_flag" || true
        sleep "$loop_sleep"
    done
}

SHARE_LOOP_PID=""
# Start share loop in background (best-effort; never blocks the main agent).
# Default ON for every profile; set SHARE_DISABLE=1 in container env to skip.
if [ "${SHARE_DISABLE:-}" = "1" ] || [ "${SHARE_DISABLE:-}" = "true" ]; then
    echo ">>> [share] disabled via SHARE_DISABLE (agent only; no groket-share.json loop)"
elif command -v python3 >/dev/null 2>&1 && command -v grok >/dev/null 2>&1; then
    _gte_share_loop &
    SHARE_LOOP_PID=$!
    echo ">>> [share] in-container share loop started (pid $SHARE_LOOP_PID) — default for all images"
else
    echo ">>> [share] python3/grok missing — no in-container share (TUI will show pending; rebuild image)"
fi

# Use stdbuf to force line-buffered stdout when available (GNU coreutils).
if command -v stdbuf >/dev/null 2>&1; then
    WRAP=(stdbuf -oL)
else
    WRAP=()
fi

# Multi-turn control files on the bind-mounted sessions volume (host can write).
TURN_DIR="${TURN_DIR:-/root/.grok/sessions/.groket-turn}"
mkdir -p "$TURN_DIR"
TURN_STATUS="$TURN_DIR/status.json"
TURN_NEXT="$TURN_DIR/next-prompt.txt"
TURN_CMD="$TURN_DIR/command" # "follow_up" | "done" (host writes)
TURN_SCRIPT="$TURN_DIR/scripted-turns.json" # optional JSON list of prompt strings

_gte_write_turn_status() {
    local state="$1"
    local session_id="${2:-}"
    local turn="${3:-0}"
    python3 - "$TURN_STATUS" "$state" "$session_id" "$turn" <<'PY' 2>/dev/null || true
import json, sys
from pathlib import Path
path, state, sid, turn = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
Path(path).write_text(
    json.dumps({"state": state, "session_id": sid, "turn": int(turn)}, indent=2) + "\n",
    encoding="utf-8",
)
PY
}

_gte_latest_session_id() {
    # Prefer deepest session dir name (session id segment).
    find /root/.grok/sessions -mindepth 2 -maxdepth 2 -type d ! -name 'compaction' \
        ! -path '*/.groket-turn/*' 2>/dev/null | sort | tail -1 | xargs -r basename
}

_gte_run_grok_turn() {
    local prompt_file="$1"
    local resume_id="${2:-}"
    # Optional 3rd arg: "fork" → --fork-session (new Grok session id from parent history).
    local resume_mode="${3:-}"
    # Optional 4th arg: UUID for --session-id when forking (names the new session).
    local fork_sid="${4:-}"
    # Strip optional model:effort if MODEL was passed as a compound token.
    local model_id="${MODEL%%:*}"
    local -a cmd=(grok -m "$model_id" --always-approve --output-format streaming-json --prompt-file "$prompt_file")
    # Headless effort (low|medium|high|xhigh|max). Prefer REASONING_EFFORT env;
    # fall back to suffix on MODEL (model:xhigh) or config.toml default.
    local effort="${REASONING_EFFORT:-}"
    if [ -z "$effort" ] && [ "$MODEL" != "$model_id" ]; then
        effort="${MODEL#*:}"
    fi
    if [ -n "$effort" ]; then
        if grok --help 2>&1 | grep -qE -- '--effort'; then
            cmd+=(--effort "$effort")
        elif grok --help 2>&1 | grep -qE -- '--reasoning-effort'; then
            cmd+=(--reasoning-effort "$effort")
        fi
    fi
    if [ -n "$resume_id" ]; then
        # Prefer explicit resume so follow-ups stay on the same Grok session.
        if grok --help 2>&1 | grep -qE -- '--resume'; then
            cmd+=(--resume "$resume_id")
            # Branch into a new session id (resume from parent history without mutating parent id).
            if [ "$resume_mode" = "fork" ] || [ "$resume_mode" = "1" ]; then
                if grok --help 2>&1 | grep -qE -- '--fork-session'; then
                    cmd+=(--fork-session)
                    if [ -n "$fork_sid" ] && grok --help 2>&1 | grep -qE -- '--session-id'; then
                        cmd+=(--session-id "$fork_sid")
                    fi
                fi
            fi
        elif grok --help 2>&1 | grep -qE -- '--continue|-c'; then
            cmd+=(--continue)
            if [ "$resume_mode" = "fork" ] || [ "$resume_mode" = "1" ]; then
                if grok --help 2>&1 | grep -qE -- '--fork-session'; then
                    cmd+=(--fork-session)
                fi
            fi
        fi
    fi
    echo ">>> Launching: ${cmd[*]}"
    set +e
    "${WRAP[@]}" "${cmd[@]}"
    local rc=$?
    set -e
    return "$rc"
}

# Scripted batch turns (non-interactive multi-prompt) from host-written JSON list.
_gte_pop_scripted_turn() {
    python3 - "$TURN_SCRIPT" <<'PY' 2>/dev/null || true
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
if not p.is_file():
    sys.exit(0)
try:
    data = json.loads(p.read_text(encoding="utf-8"))
except Exception:
    sys.exit(0)
if not isinstance(data, list) or not data:
    sys.exit(0)
prompt = data.pop(0)
p.write_text(json.dumps(data) + "\n", encoding="utf-8")
print(prompt, end="")
PY
}

AGENT_EXIT=0
SESSION_ID=""
TURN_INDEX=0
INTERACTIVE="${INTERACTIVE:-0}"
# Host-seeded ended session: first turn uses grok --resume (same as later multi-turns).
RESUME_SESSION_ID="${RESUME_SESSION_ID:-}"

# Do NOT exec: we need a final share after the agent exits (exec would kill this shell
# and the share loop, leaving the share page mid-turn without the last assistant msg).
while true; do
    TURN_INDEX=$((TURN_INDEX + 1))
    if [ "$TURN_INDEX" -eq 1 ]; then
        if [ -n "$RESUME_SESSION_ID" ]; then
            echo ">>> Resuming Grok session id=$RESUME_SESSION_ID"
            SESSION_ID="$RESUME_SESSION_ID"
            _gte_run_grok_turn "$PROMPT_FILE" "$RESUME_SESSION_ID"
        else
            _gte_run_grok_turn "$PROMPT_FILE" ""
        fi
        AGENT_EXIT=$?
    else
        _gte_run_grok_turn "$PROMPT_FILE" "$SESSION_ID"
        AGENT_EXIT=$?
    fi
    SESSION_ID="$(_gte_latest_session_id || true)"
    # Prefer explicit resume id when discovery fails (seeded session still valid).
    if [ -z "$SESSION_ID" ] && [ -n "$RESUME_SESSION_ID" ]; then
        SESSION_ID="$RESUME_SESSION_ID"
    fi
    echo ">>> Agent turn $TURN_INDEX exited with code $AGENT_EXIT (session=${SESSION_ID:-unknown})"
    # Mid-run chown so live TUI / host tools see non-root prompt_history.jsonl etc.
    _gte_fix_sessions_ownership

    # Non-interactive scripted follow-ups (batch tasks.turns)
    scripted="$(_gte_pop_scripted_turn || true)"
    if [ -n "$scripted" ]; then
        printf '%s' "$scripted" > "$PROMPT_FILE"
        echo ">>> Scripted follow-up turn queued ($(wc -c < "$PROMPT_FILE") bytes)"
        continue
    fi

    # Host sent a "last turn" follow-up — do not await further prompts.
    if [ -f "$TURN_DIR/final_turn" ]; then
        echo ">>> Final-turn flag set — not awaiting further follow-ups"
        rm -f "$TURN_DIR/final_turn" "$TURN_CMD" "$TURN_NEXT"
        _gte_write_turn_status "done" "$SESSION_ID" "$TURN_INDEX"
        break
    fi

    # Interactive: wait for host follow-up or done
    if [ "$INTERACTIVE" = "1" ] || [ "$INTERACTIVE" = "true" ]; then
        rm -f "$TURN_CMD" "$TURN_NEXT"
        _gte_write_turn_status "awaiting_follow_up" "$SESSION_ID" "$TURN_INDEX"
        echo ">>> Waiting for follow-up (write $TURN_NEXT + $TURN_CMD=follow_up|done)"
        idle_max="${INTERACTIVE_IDLE_SECS:-86400}"
        waited=0
        while [ "$waited" -lt "$idle_max" ]; do
            if [ -f "$TURN_CMD" ]; then
                cmd=$(tr -d '[:space:]' < "$TURN_CMD" | tr '[:upper:]' '[:lower:]')
                if [ "$cmd" = "done" ]; then
                    echo ">>> Host marked interactive session done"
                    _gte_write_turn_status "done" "$SESSION_ID" "$TURN_INDEX"
                    break 2
                fi
                if [ "$cmd" = "follow_up" ] && [ -s "$TURN_NEXT" ]; then
                    # Preserve final_turn across rm of command/next (host may set it).
                    FINAL_TURN=0
                    [ -f "$TURN_DIR/final_turn" ] && FINAL_TURN=1
                    cp "$TURN_NEXT" "$PROMPT_FILE"
                    rm -f "$TURN_CMD" "$TURN_NEXT"
                    [ "$FINAL_TURN" = "1" ] && printf '1\n' > "$TURN_DIR/final_turn"
                    _gte_write_turn_status "running" "$SESSION_ID" "$TURN_INDEX"
                    continue 2
                fi
            fi
            sleep 2
            waited=$((waited + 2))
        done
        echo ">>> Interactive idle timeout — finishing"
        _gte_write_turn_status "timeout" "$SESSION_ID" "$TURN_INDEX"
    fi
    break
done

echo ">>> Agent finished (last exit $AGENT_EXIT) — stopping share loop, then final snapshot"
if [ -n "$SHARE_LOOP_PID" ]; then
    kill "$SHARE_LOOP_PID" 2>/dev/null || true
    wait "$SHARE_LOOP_PID" 2>/dev/null || true
fi
if [ "${SHARE_DISABLE:-}" = "1" ] || [ "${SHARE_DISABLE:-}" = "true" ]; then
    echo ">>> [share] final snapshot skipped (SHARE_DISABLE)"
else
    # Settle so last chat/events/summary flush to the bind mount before share reads them.
    sleep "${SHARE_FINAL_DELAY_SECS:-5}"
    _gte_share_once 1 || true
    # Second pass: share CLI sometimes needs a beat after the first force snapshot.
    sleep 2
    _gte_share_once 1 || true
fi

_gte_fix_sessions_ownership
exit "$AGENT_EXIT"

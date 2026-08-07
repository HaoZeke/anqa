"""HUD live-refresh pure helpers (Node ``node --test`` on ``live.test.js``)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HUD = REPO / "groket-hud"
LIVE_TEST = HUD / "src" / "live.test.js"


def _nodes_from_asdf_shim(shim: Path) -> list[str]:
    """Resolve real node binaries from an asdf shim (HOME may be test-isolated)."""
    found: list[str] = []
    try:
        text = shim.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return found
    ver = ""
    for line in text.splitlines()[:20]:
        m = re.search(r"asdf-plugin:\s*nodejs\s+(\S+)", line)
        if m:
            ver = m.group(1)
            break
    if shim.parent.name != "shims":
        return found
    asdf_root = shim.parent.parent
    if ver:
        real = asdf_root / "installs" / "nodejs" / ver / "bin" / "node"
        if real.is_file():
            found.append(str(real))
    # Any installed version as fallback
    installs = asdf_root / "installs" / "nodejs"
    if installs.is_dir():
        found.extend(str(p) for p in sorted(installs.glob("*/bin/node"), reverse=True))
    return found


def _candidate_nodes() -> list[str]:
    """Ordered candidate Node binaries (prefer real installs over broken shims)."""
    out: list[str] = []
    which = shutil.which("node")
    if which:
        out.extend(_nodes_from_asdf_shim(Path(which)))
        out.append(which)
    data_dir = os.environ.get("ASDF_DATA_DIR", "").strip()
    if data_dir:
        root = Path(data_dir) / "installs" / "nodejs"
        if root.is_dir():
            out.extend(str(p) for p in sorted(root.glob("*/bin/node"), reverse=True))
    for fixed in (Path("/usr/local/bin/node"), Path("/opt/homebrew/bin/node")):
        if fixed.is_file():
            out.append(str(fixed))
    seen: set[str] = set()
    unique: list[str] = []
    for c in out:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


def _working_node() -> str | None:
    """Return a Node binary that can execute scripts under the test environment."""
    for cand in _candidate_nodes():
        p = Path(cand)
        if not p.is_file() or not os.access(p, os.X_OK):
            continue
        try:
            check = subprocess.run(
                [cand, "-e", "process.stdout.write('ok')"],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if check.returncode == 0 and (check.stdout or "").strip() == "ok":
            return cand
    return None


def test_hud_live_js_helpers() -> None:
    """Ship path: ``groket-hud/src/live.js`` covered by Node's test runner."""
    assert LIVE_TEST.is_file()
    node = _working_node()
    if node is None:
        pytest.skip("no working node binary (install nodejs)")
    proc = subprocess.run(
        [node, "--test", str(LIVE_TEST)],
        cwd=str(HUD),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert proc.returncode == 0, f"node={node}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"

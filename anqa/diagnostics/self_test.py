"""Host dependency checks for the review product.

Probes config home, the session catalog, leftover prefs, and the HUD
seat. Used by ``anqa doctor`` and the in-app self-test modal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..models import JsonObject, as_json_object


@dataclass(frozen=True)
class CheckResult:
    """One self-test row."""

    id: str
    name: str
    ok: bool
    detail: str = ""
    required: bool = True  # False = advisory (warn, not fail overall)

    @property
    def level(self) -> str:
        if self.ok:
            return "ok"
        return "error" if self.required else "warn"


@dataclass
class SelfTestReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks if c.required)

    @property
    def warn_count(self) -> int:
        return sum(1 for c in self.checks if not c.ok and not c.required)

    @property
    def fail_count(self) -> int:
        return sum(1 for c in self.checks if not c.ok and c.required)

    def lines(self) -> list[str]:
        out: list[str] = []
        for c in self.checks:
            mark = "OK" if c.ok else ("WARN" if not c.required else "FAIL")
            line = f"[{mark}] {c.name}"
            if c.detail:
                line += f" — {c.detail}"
            out.append(line)
        summary = "PASS" if self.ok else "FAIL"
        out.append(
            f"Result: {summary}  (required fails={self.fail_count}, warnings={self.warn_count})"
        )
        return out


def _check_app_home() -> CheckResult:
    from ..paths import app_home

    root = app_home()
    try:
        probe = root / ".anqa-write-probe"
        probe.write_text("ok\n", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return CheckResult(
            id="app_home",
            name="Config home writable",
            ok=True,
            detail=str(root),
            required=True,
        )
    except OSError as exc:
        return CheckResult(
            id="app_home",
            name="Config home writable",
            ok=False,
            detail=f"{root}: {exc}",
            required=True,
        )


def _check_catalog_store(catalog_root: Path | None) -> CheckResult:
    from ..paths import default_host_sessions_root

    root = Path(catalog_root).expanduser() if catalog_root else default_host_sessions_root()
    if root.is_dir():
        return CheckResult(
            id="catalog",
            name="Session store",
            ok=True,
            detail=str(root),
            required=False,
        )
    return CheckResult(
        id="catalog",
        name="Session store",
        ok=False,
        detail=f"{root} missing",
        required=False,
    )


def _check_session_display() -> CheckResult:
    """Advisory: which display protocol the seat is using."""
    import os

    wayland = (os.environ.get("WAYLAND_DISPLAY") or "").strip()
    x11 = (os.environ.get("DISPLAY") or "").strip()
    if wayland:
        detail = f"Wayland ({wayland})"
        if x11:
            detail += f"; Xwayland DISPLAY={x11}"
        detail += (
            " — HUD summon: anqa desktop --toggle (forwards XDG_ACTIVATION_TOKEN) "
            "/ tray (no X11 hotkey)"
        )
        return CheckResult(
            id="session_display",
            name="Session display",
            ok=True,
            detail=detail,
            required=False,
        )
    if x11:
        return CheckResult(
            id="session_display",
            name="Session display",
            ok=True,
            detail=f"X11 ({x11}) — in-process global hotkey available",
            required=False,
        )
    return CheckResult(
        id="session_display",
        name="Session display",
        ok=False,
        detail="neither WAYLAND_DISPLAY nor DISPLAY set — HUD needs a graphical seat",
        required=False,
    )


def _check_sway_socket() -> CheckResult:
    """Advisory: Sway IPC socket when on a Sway seat."""
    import os

    sock = (os.environ.get("SWAYSOCK") or "").strip()
    if not sock:
        return CheckResult(
            id="sway_socket",
            name="Sway IPC (SWAYSOCK)",
            ok=True,
            detail="unset (not a Sway session, or nested shell without env)",
            required=False,
        )
    path = Path(sock)
    if path.exists():
        return CheckResult(
            id="sway_socket",
            name="Sway IPC (SWAYSOCK)",
            ok=True,
            detail=f"{path} — overlay place (float/center); focus is xdg-activation",
            required=False,
        )
    return CheckResult(
        id="sway_socket",
        name="Sway IPC (SWAYSOCK)",
        ok=False,
        detail=f"SWAYSOCK set but missing: {path}",
        required=False,
    )


def _check_hud_summon_socket() -> CheckResult:
    """Advisory: whether a long-lived HUD is accepting compositor summon commands."""
    from ..hud.launch import default_summon_socket_path, summon_socket_accepts

    path = default_summon_socket_path()
    if summon_socket_accepts(path):
        return CheckResult(
            id="hud_summon",
            name="HUD summon socket",
            ok=True,
            detail=f"listening at {path} (anqa desktop --toggle)",
            required=False,
        )
    return CheckResult(
        id="hud_summon",
        name="HUD summon socket",
        ok=False,
        detail=f"not listening ({path}) — start with: anqa desktop",
        required=False,
    )


def _check_leftover_json_config() -> CheckResult:
    """Warn when a sibling ``config.json`` exists; prefs live in ``config.toml``."""
    from ..config import leftover_json_config_path, load_app_config
    from ..paths import app_config_path

    load_app_config()
    old = leftover_json_config_path()
    new = app_config_path()
    if old.is_file():
        return CheckResult(
            id="config-toml",
            name="App prefs",
            ok=False,
            required=False,
            detail=f"{old} exists; prefs are {new}",
        )
    return CheckResult(
        id="config-toml",
        name="App prefs",
        ok=True,
        required=False,
        detail=str(new),
    )


def _query_owner_diagnostics(socket_path: Path) -> JsonObject | None:
    """Ask a live owner for active RPC + last failure. None when unreachable."""
    import json
    import socket

    from ..control.contract import PROTOCOL_VERSION

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(1.0)
    try:
        sock.connect(str(socket_path))

        def _call(payload: JsonObject) -> JsonObject:
            sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            buf = b""
            while b"\n" not in buf:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
            raw = json.loads(buf.decode("utf-8") or "{}")
            return as_json_object(raw) if isinstance(raw, dict) else {}

        init = _call(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": PROTOCOL_VERSION},
            }
        )
        if "error" in init:
            return None
        resp = _call({"jsonrpc": "2.0", "id": 2, "method": "diagnostics", "params": {}})
        result = resp.get("result")
        return as_json_object(result) if isinstance(result, dict) else None
    except OSError:
        return None
    finally:
        sock.close()


def _check_control_rpc(control_socket: Path | None) -> CheckResult:
    """Advisory: live owner active RPC and last bounded failure."""
    from ..control.server import default_socket_path

    path = (
        Path(control_socket).expanduser() if control_socket is not None else default_socket_path()
    )
    if not path.exists():
        return CheckResult(
            id="control_rpc",
            name="Control owner RPC",
            ok=True,
            required=False,
            detail=f"no owner at {path}",
        )
    snap = _query_owner_diagnostics(path)
    if snap is None:
        return CheckResult(
            id="control_rpc",
            name="Control owner RPC",
            ok=True,
            required=False,
            detail=f"owner not answering at {path}",
        )
    active_raw = snap.get("active")
    fail_raw = snap.get("lastFailure")
    parts: list[str] = []
    if isinstance(active_raw, list) and active_raw:
        names = [
            str(item.get("method"))
            for item in active_raw
            if isinstance(item, dict) and item.get("method")
        ]
        parts.append(f"active={', '.join(names)}" if names else "active=none")
    else:
        parts.append("active=none")
    if isinstance(fail_raw, dict) and fail_raw:
        method = str(fail_raw.get("method") or "")
        message = str(fail_raw.get("message") or "")
        parts.append(f"lastFailure={method}: {message}".strip())
        return CheckResult(
            id="control_rpc",
            name="Control owner RPC",
            ok=False,
            required=False,
            detail="; ".join(parts),
        )
    return CheckResult(
        id="control_rpc",
        name="Control owner RPC",
        ok=True,
        required=False,
        detail="; ".join(parts),
    )


def run_self_test(
    *,
    catalog_root: Path | None = None,
    control_socket: Path | None = None,
) -> SelfTestReport:
    """Run all host checks. Safe to call from UI worker threads."""
    checks = [
        _check_app_home(),
        _check_catalog_store(catalog_root),
        _check_session_display(),
        _check_sway_socket(),
        _check_hud_summon_socket(),
        _check_leftover_json_config(),
        _check_control_rpc(control_socket),
    ]
    return SelfTestReport(checks=checks)

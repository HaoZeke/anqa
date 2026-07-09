"""Resume seed: staging tree + live symlink for Grok."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from groket.runs.launch_meta import LAUNCH_META_FILENAME
from groket.session.resume import (
    RESUME_SEED_DIRNAME,
    can_resume_session,
    is_resume_seed_path,
    resume_cwd_token,
    resume_session_id,
    seed_resume_into_traces_vol,
)


def _fake_session(root: Path, *, sid: str = "sess-abc") -> Path:
    token = "%2Fworkspace"
    session = root / "traces" / "groket-old" / token / sid
    session.mkdir(parents=True)
    (session / "chat_history.jsonl").write_text(
        '{"role":"user","content":"hi"}\n', encoding="utf-8"
    )
    (session / "summary.json").write_text("{}", encoding="utf-8")
    (session.parent / "prompt_history.jsonl").write_text("p\n", encoding="utf-8")
    return session


def test_resume_session_id_and_cwd_token(tmp_path: Path) -> None:
    s = _fake_session(tmp_path)
    assert resume_session_id(s) == "sess-abc"
    assert resume_cwd_token(s) == "%2Fworkspace"
    assert can_resume_session(s) is True
    assert can_resume_session(tmp_path / "missing") is False


def test_seed_layout_is_staging_plus_live_symlink(tmp_path: Path) -> None:
    source = _fake_session(tmp_path)
    dest_vol = tmp_path / "traces" / "groket-new"
    dest_vol.mkdir(parents=True)
    sid = seed_resume_into_traces_vol(dest_vol, source)
    assert sid == "sess-abc"
    seed = dest_vol / RESUME_SEED_DIRNAME / "%2Fworkspace" / "sess-abc"
    live = dest_vol / "%2Fworkspace" / "sess-abc"
    assert (seed / "chat_history.jsonl").is_file()
    assert live.is_symlink()
    assert live.resolve() == seed.resolve()
    assert is_resume_seed_path(seed) is True
    assert is_resume_seed_path(live) is True
    assert (source / "chat_history.jsonl").is_file()


def test_find_sessions_skips_resume_seed_and_live_link(tmp_path: Path) -> None:
    """Substrate and its live symlink are not operator eval rows."""
    from groket.parser import find_sessions

    source = _fake_session(tmp_path)
    dest_vol = tmp_path / "traces" / "groket-new"
    dest_vol.mkdir(parents=True)
    seed_resume_into_traces_vol(dest_vol, source)
    child = dest_vol / "%2Fworkspace" / "forked-child-id"
    child.mkdir(parents=True)
    (child / "summary.json").write_text("{}", encoding="utf-8")

    found = {p.name for p in find_sessions(dest_vol)}
    assert "sess-abc" not in found
    assert "forked-child-id" in found


def test_seed_resume_rejects_empty_session(tmp_path: Path) -> None:
    empty = tmp_path / "empty-sess"
    empty.mkdir()
    with pytest.raises(ValueError, match="no chat"):
        seed_resume_into_traces_vol(tmp_path / "vol", empty)


def test_seed_resume_missing_source(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        seed_resume_into_traces_vol(tmp_path / "vol", tmp_path / "gone")


def test_seed_resume_overwrites_existing_and_strips_locks(tmp_path: Path) -> None:
    source = _fake_session(tmp_path)
    (source / "summary.json.lock").write_text("x", encoding="utf-8")
    dest_vol = tmp_path / "traces" / "groket-new"
    dest_vol.mkdir(parents=True)
    seed_resume_into_traces_vol(dest_vol, source)
    # Re-seed replaces substrate
    seed_resume_into_traces_vol(dest_vol, source)
    seed = dest_vol / RESUME_SEED_DIRNAME / "%2Fworkspace" / "sess-abc"
    assert not (seed / "summary.json.lock").exists()
    assert (seed / "chat_history.jsonl").is_file()


def test_resume_cwd_token_fallback(tmp_path: Path) -> None:
    flat = tmp_path / "sess-flat"
    flat.mkdir()
    (flat / "events.jsonl").write_text("{}\n", encoding="utf-8")
    assert resume_cwd_token(flat) == "%2Fworkspace"
    assert can_resume_session(flat) is True


def test_seed_lock_unlink_and_hist_copy_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _fake_session(tmp_path)
    (source / "summary.json.lock").write_text("x", encoding="utf-8")
    dest_vol = tmp_path / "vol"
    dest_vol.mkdir()

    real_unlink = Path.unlink
    real_copy2 = shutil.copy2

    def boom_unlink(self, *a, **k):
        if str(self).endswith(".lock"):
            raise OSError("busy")
        return real_unlink(self, *a, **k)

    def boom_copy2(src, dst, *a, **k):
        if "prompt_history" in str(src):
            raise OSError("no hist")
        return real_copy2(src, dst, *a, **k)

    monkeypatch.setattr(Path, "unlink", boom_unlink)
    monkeypatch.setattr(shutil, "copy2", boom_copy2)
    sid = seed_resume_into_traces_vol(dest_vol, source)
    assert sid == "sess-abc"


def test_seed_empty_session_id_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _fake_session(tmp_path)
    monkeypatch.setattr(
        "groket.session.resume.resume_session_id",
        lambda _p: "",
    )
    with pytest.raises(ValueError, match="empty session id"):
        seed_resume_into_traces_vol(tmp_path / "vol", source)


def test_start_container_seed_failure_propagates(tmp_path: Path) -> None:
    from groket.docker.orchestrator import ContainerConfig, DockerOrchestrator

    class FakeDocker:
        def run(self, *a, **k):
            raise AssertionError("should not run")

    o = DockerOrchestrator(tmp_path / "runs")
    o._docker = FakeDocker()  # type: ignore[assignment]
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[cli]\n", encoding="utf-8")
    cfg = ContainerConfig(
        model="v9",
        prompt="x",
        container_name="groket-resume-fail",
        resume_source_dir=str(tmp_path / "missing-session"),
    )
    with pytest.raises(FileNotFoundError):
        o.start_container(cfg, "fake-image:tag", auth, cfg_path)


def test_start_container_sets_resume_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from groket.docker import orchestrator as orch_mod
    from groket.docker.orchestrator import ContainerConfig, DockerOrchestrator

    source = _fake_session(tmp_path)
    captured: dict = {}

    class FakeDocker:
        def run(self, *a, **k):
            captured["envs"] = k.get("envs") or {}

            class C:
                id = "deadbeefdead"

            return C()

    o = DockerOrchestrator(tmp_path / "runs")
    o._docker = FakeDocker()  # type: ignore[assignment]
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[cli]\n", encoding="utf-8")
    monkeypatch.setattr(orch_mod, "share_once_py", lambda: "")
    monkeypatch.setattr(orch_mod, "entrypoint_sh", lambda: "#!/bin/bash\n")
    monkeypatch.setattr(orch_mod, "empty_setup_sh", lambda: "#!/bin/bash\n")

    cfg = ContainerConfig(
        model="v9",
        prompt="continue please",
        container_name="groket-resume-test",
        interactive=True,
        resume_source_dir=str(source),
        resume_session_id="sess-abc",
        resume_fork_session_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        run_id="rid1",
    )
    o.start_container(cfg, "fake-image:tag", auth, cfg_path)
    assert captured["envs"].get("RESUME_SESSION_ID") == "sess-abc"
    assert captured["envs"].get("RESUME_FORK") == "1"
    assert captured["envs"].get("FORK_SESSION_ID") == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    traces = tmp_path / "runs" / "traces" / "groket-resume-test"
    seed = traces / RESUME_SEED_DIRNAME / "%2Fworkspace" / "sess-abc"
    live = traces / "%2Fworkspace" / "sess-abc"
    assert (seed / "chat_history.jsonl").is_file()
    assert live.is_symlink()
    assert "continue please" in (traces / "groket-prompt.txt").read_text(encoding="utf-8")
    launch = json.loads((traces / LAUNCH_META_FILENAME).read_text(encoding="utf-8"))
    assert launch["resume_parent_session_id"] == "sess-abc"

"""Resume seed: copy ended session into a new traces volume."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from groket.session.resume import (
    can_resume_session,
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


def test_seed_resume_into_traces_vol_copies_layout(tmp_path: Path) -> None:
    source = _fake_session(tmp_path)
    dest_vol = tmp_path / "traces" / "groket-new"
    dest_vol.mkdir(parents=True)
    sid = seed_resume_into_traces_vol(dest_vol, source)
    assert sid == "sess-abc"
    seeded = dest_vol / "%2Fworkspace" / "sess-abc"
    assert (seeded / "chat_history.jsonl").is_file()
    assert (seeded / "summary.json").is_file()
    assert (dest_vol / "%2Fworkspace" / "prompt_history.jsonl").is_file()
    # Source untouched
    assert (source / "chat_history.jsonl").is_file()


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
    # Pre-existing dest should be replaced
    stale = dest_vol / "%2Fworkspace" / "sess-abc"
    stale.mkdir(parents=True)
    (stale / "old.txt").write_text("stale", encoding="utf-8")
    seed_resume_into_traces_vol(dest_vol, source)
    seeded = dest_vol / "%2Fworkspace" / "sess-abc"
    assert not (seeded / "old.txt").exists()
    assert not (seeded / "summary.json.lock").exists()
    assert (seeded / "chat_history.jsonl").is_file()


def test_resume_cwd_token_fallback(tmp_path: Path) -> None:
    """Flat session dirs (no encoded parent) use %2Fworkspace."""
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
            captured["volumes"] = k.get("volumes") or []

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
    assert captured["envs"].get("INTERACTIVE") == "1"
    traces = tmp_path / "runs" / "traces" / "groket-resume-test"
    assert (traces / "%2Fworkspace" / "sess-abc" / "chat_history.jsonl").is_file()
    assert "continue please" in (traces / "groket-prompt.txt").read_text(encoding="utf-8")

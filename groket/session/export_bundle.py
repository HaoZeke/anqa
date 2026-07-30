"""Export a session run as a portable outer tarball.

Outer archive layout (under ``~/.groket/reports/`` by default).

Always present::

    grok-trace.tar.gz       # exact ``grok trace --local`` archive (nested)
    README.txt
    manifest.json

Present only when the data exists::

    run/                    # eval volume (recipe, launch, prompt, turn gate, …)
    analysis/               # cached analysis plugin JSON + markdown reports
    flags.json              # operator flags (session or config-home fallback)
    notes/                  # operator_notes.toml from the notes store

``grok-trace.tar.gz`` is **only** produced by the Grok CLI
(``grok trace --local``). There is no groket session-copy fallback: if the CLI
is missing or fails, export raises.

The nested archive has the official layout::

    <session_id>/export_metadata.json
    <session_id>/trace_config.json
    <session_id>/summary.json
    <session_id>/events.jsonl
    <session_id>/chat_history.jsonl
    <session_id>/prompt_context.json
    <session_id>/system_prompt.txt
    … plus any other files the CLI packs from the session directory
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from shutil import which

from ..models import JsonObject, JsonValue, as_json_object, json_as_object
from ..notes import collect_notes_for_export
from ..paths import analysis_cache_dir, is_run_dir_name, reports_dir

logger = logging.getLogger(__name__)

# Nested member: official grok-trace archive inside the operator export bundle.
GROK_TRACE_ARCHIVE_NAME = "grok-trace.tar.gz"

# Top-level names under a run volume that are not exported (noise / huge / other sessions).
_RUN_SKIP_NAMES = frozenset(
    {
        "session_search.sqlite",
        "session_search.sqlite-wal",
        "session_search.sqlite-shm",
    }
)

# Core members always present in official ``grok trace`` archives (even if empty).
_GROK_TRACE_CORE_FILES = frozenset(
    {
        "export_metadata.json",
        "trace_config.json",
        "summary.json",
        "events.jsonl",
        "chat_history.jsonl",
        "prompt_context.json",
        "system_prompt.txt",
    }
)

# Manifest schema for this outer bundle layout (bump when fields/layout change).
_MANIFEST_SCHEMA = 6


@dataclass
class ExportBundleResult:
    """Outcome of :func:`export_session_bundle`."""

    path: Path
    session_id: str
    arcnames: list[str] = field(default_factory=list)


def run_volume_for_session(session_dir: Path) -> Path | None:
    """Return the ``runs/traces/<container>/`` volume for *session_dir*, if any."""
    p = Path(session_dir).expanduser().resolve()
    for anc in p.parents:
        if is_run_dir_name(anc.name):
            return anc
        if anc.name == "traces":
            break
    return None


def default_bundle_path(session_id: str, *, dest_dir: Path | None = None) -> Path:
    """Default outer archive path under reports dir."""
    root = Path(dest_dir) if dest_dir is not None else reports_dir()
    root.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in (session_id or "session"))
    return root / f"{safe}-{ts}.tar.gz"


def _session_id(session_dir: Path) -> str:
    return Path(session_dir).name.strip() or "session"


def build_grok_trace_archive(session_dir: Path, out_tar: Path) -> None:
    """Write *out_tar* via ``grok trace --local`` only (exact CLI bytes).

    Links *session_dir* into ``~/.grok/sessions`` so the CLI can resolve the
    session id, then copies the CLI output as-is.

    :raises RuntimeError: ``grok`` missing, link failure, CLI error, or empty output.
    """
    grok = which("grok")
    if not grok:
        raise RuntimeError(
            "grok CLI not found on PATH; session export requires "
            "`grok trace --local` (no fallback packer)"
        )
    sid = _session_id(session_dir)
    session_dir = Path(session_dir).expanduser().resolve()
    if not session_dir.is_dir():
        raise RuntimeError(f"session directory not found: {session_dir}")
    out_tar = Path(out_tar)
    out_tar.parent.mkdir(parents=True, exist_ok=True)

    sessions_root = Path.home() / ".grok" / "sessions"
    probe = sessions_root / f"%2Ftmp%2Fgroket-export-{os.getpid()}-{sid[:8]}"
    link = probe / sid
    try:
        probe.mkdir(parents=True, exist_ok=True)
        if link.exists() or link.is_symlink():
            if link.is_symlink() or link.is_file():
                link.unlink()
            else:
                shutil.rmtree(link)
        link.symlink_to(session_dir, target_is_directory=True)
        proc = subprocess.run(
            [grok, "trace", "--local", sid, "-o", str(out_tar)],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        err = (proc.stderr or proc.stdout or "").strip()
        if proc.returncode != 0 or not out_tar.is_file() or out_tar.stat().st_size <= 0:
            if out_tar.is_file():
                try:
                    out_tar.unlink()
                except OSError:
                    pass
            detail = err[:500] if err else "empty output"
            raise RuntimeError(f"grok trace --local failed (rc={proc.returncode}): {detail}")
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError) as exc:
        if out_tar.is_file():
            try:
                out_tar.unlink()
            except OSError:
                pass
        raise RuntimeError(f"grok trace --local error: {exc}") from exc
    finally:
        try:
            if link.is_symlink() or link.is_file():
                link.unlink(missing_ok=True)
            elif link.is_dir():
                shutil.rmtree(link, ignore_errors=True)
            if probe.is_dir():
                try:
                    probe.rmdir()
                except OSError:
                    shutil.rmtree(probe, ignore_errors=True)
        except OSError:
            logger.debug("cleanup of grok sessions probe failed", exc_info=True)


def grok_trace_member_paths(session_id: str) -> frozenset[str]:
    """Archive member paths for the official core grok-trace files."""
    sid = (session_id or "").strip() or "session"
    return frozenset(f"{sid}/{name}" for name in _GROK_TRACE_CORE_FILES)


def assert_grok_trace_archive_shape(trace_tar: Path, session_id: str) -> list[str]:
    """Validate *trace_tar* is an official-shaped grok-trace archive.

    :returns: Sorted member names inside the archive.
    :raises RuntimeError: Layout does not match ``grok trace`` output.
    """
    sid = (session_id or "").strip() or "session"
    path = Path(trace_tar)
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"grok-trace archive missing or empty: {path}")
    try:
        with tarfile.open(path, "r:gz") as tf:
            names = [m.name for m in tf.getmembers() if m.name]
    except tarfile.TarError as exc:
        raise RuntimeError(f"invalid grok-trace archive: {path}: {exc}") from exc

    prefix = f"{sid}/"
    if not any(n == sid or n.startswith(prefix) for n in names):
        raise RuntimeError(
            f"grok-trace archive must root under {sid}/ (got tops: "
            f"{sorted({n.split('/')[0] for n in names})[:8]})"
        )
    foreign = sorted(
        {n.split("/")[0] for n in names if n and n != sid and not n.startswith(prefix)}
    )
    if foreign:
        raise RuntimeError(f"grok-trace archive has unexpected top-level members: {foreign}")
    missing = sorted(grok_trace_member_paths(sid) - set(names))
    if missing:
        raise RuntimeError(f"grok-trace archive missing official core files: {missing}")
    return sorted(names)


def _add_tree(tf: tarfile.TarFile, src: Path, arc_prefix: str) -> list[str]:
    """Add *src* file or directory under *arc_prefix*; return arcnames."""
    names: list[str] = []
    src = Path(src)
    if not src.exists():
        return names
    if src.is_file():
        arc = arc_prefix.rstrip("/")
        tf.add(src, arcname=arc)
        names.append(arc)
        return names
    for path in sorted(src.rglob("*")):
        if path.is_symlink() or path.is_file():
            rel = path.relative_to(src).as_posix()
            arc = f"{arc_prefix.rstrip('/')}/{rel}"
            tf.add(path, arcname=arc)
            names.append(arc)
        elif path.is_dir() and not any(path.iterdir()):
            rel = path.relative_to(src).as_posix()
            arc = f"{arc_prefix.rstrip('/')}/{rel}"
            tf.add(path, arcname=arc)
            names.append(arc)
    return names


def _staging_member_paths(staging: Path, *, skip: frozenset[str] | set[str]) -> list[str]:
    """Relative paths under *staging* that will be packed (same rules as :func:`_add_tree`)."""
    names: list[str] = []
    for path in sorted(staging.iterdir()):
        if path.name in skip:
            continue
        if path.is_file():
            names.append(path.name)
        elif path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_symlink() or child.is_file():
                    rel = child.relative_to(staging).as_posix()
                    names.append(rel)
                elif child.is_dir() and not any(child.iterdir()):
                    rel = child.relative_to(staging).as_posix()
                    names.append(rel)
    return names


def _collect_run_volume_files(run_vol: Path, staging: Path) -> None:
    """Copy run-volume artifacts into *staging* (excludes nested session trees)."""
    dest = staging / "run"
    copied = False
    for child in sorted(run_vol.iterdir()):
        name = child.name
        if name in _RUN_SKIP_NAMES:
            continue
        if name.endswith(".stage"):
            continue
        if name.startswith("%2F") or name in ("workspace",):
            ph = child / "prompt_history.jsonl"
            if ph.is_file():
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ph, dest / "prompt_history.jsonl")
                copied = True
            continue
        if child.is_file():
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, dest / name)
            copied = True
        elif child.is_dir() and name.startswith("."):
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copytree(child, dest / name, symlinks=True, dirs_exist_ok=True)
            copied = True
    if not copied and dest.is_dir():
        try:
            dest.rmdir()
        except OSError:
            pass


def _safe_report_stem(name: str) -> str:
    """Filesystem-safe stem for an analysis plugin cache filename."""
    stem = Path(name).stem.strip() or "analysis"
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in stem)[:120]


def _analysis_result_payload(raw: JsonValue) -> JsonObject | None:
    """Return the analysis result object from a cache file payload."""
    if not isinstance(raw, dict):
        return None
    result = raw.get("result")
    if isinstance(result, dict):
        return json_as_object(result)
    # Older / bare result files.
    if "analyzer_id" in raw or "findings" in raw or "summary" in raw:
        return json_as_object(raw)
    return None


def _markdown_from_analysis_result(result: JsonObject, *, plugin_stem: str) -> str:
    """Build a markdown report for one analysis result.

    Prefers the plugin's ``artifacts["report"]`` string when present; otherwise
    synthesises a short report from summary + findings.
    """
    artifacts = result.get("artifacts")
    if isinstance(artifacts, dict):
        report = artifacts.get("report")
        if isinstance(report, str) and report.strip():
            text = report if report.endswith("\n") else report + "\n"
            return text

    analyzer_id = str(result.get("analyzer_id") or plugin_stem or "analysis").strip()
    title = f"# Analysis report — {analyzer_id}"
    lines: list[str] = [title, ""]
    ok = result.get("ok")
    if ok is False:
        lines.append("**Status:** failed")
        err = str(result.get("error") or "").strip()
        if err:
            lines.append("")
            lines.append(err)
        lines.append("")
    summary = str(result.get("summary") or "").strip()
    if summary:
        lines.extend(["## Summary", "", summary, ""])
    findings = result.get("findings")
    if isinstance(findings, list) and findings:
        lines.extend(["## Findings", ""])
        for i, item in enumerate(findings, start=1):
            if not isinstance(item, dict):
                continue
            ftitle = str(item.get("title") or f"Finding {i}").strip()
            sev = str(item.get("severity") or "").strip().upper()
            head = f"### {i}. {ftitle}"
            if sev:
                head += f" ({sev})"
            lines.append(head)
            lines.append("")
            detail = str(item.get("detail") or "").strip()
            if detail:
                lines.append(detail)
                lines.append("")
            category = str(item.get("category") or "").strip()
            if category:
                lines.append(f"- **Category:** {category}")
            ev = item.get("event_indices")
            if isinstance(ev, list) and ev:
                bits = ", ".join(f"#{x}" for x in ev[:20])
                lines.append(f"- **Evidence:** {bits}")
            if category or (isinstance(ev, list) and ev):
                lines.append("")
    elif not summary and ok is not False:
        lines.append("_No findings or report artifact for this analyzer._")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _load_analysis_cache_json(path: Path) -> JsonValue | None:
    """Parse an analysis cache JSON file as :data:`JsonValue`."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        logger.debug("skip analysis markdown for %s", path, exc_info=True)
        return None
    try:
        data: JsonValue = json.loads(text)
    except json.JSONDecodeError:
        logger.debug("skip analysis markdown for %s", path, exc_info=True)
        return None
    return data


def _write_analysis_markdown_reports(analysis_dir: Path) -> None:
    """Write ``*.md`` next to each analysis ``*.json`` cache file."""
    if not analysis_dir.is_dir():
        return
    for path in sorted(analysis_dir.glob("*.json")):
        raw = _load_analysis_cache_json(path)
        if raw is None:
            continue
        result = _analysis_result_payload(raw)
        if result is None:
            continue
        stem = _safe_report_stem(path.name)
        md_path = analysis_dir / f"{stem}.md"
        body = _markdown_from_analysis_result(result, plugin_stem=stem)
        try:
            md_path.write_text(body, encoding="utf-8")
        except OSError:
            logger.debug("failed to write analysis markdown %s", md_path, exc_info=True)


def _collect_analysis(session_id: str, staging: Path, cache_root: Path | None) -> None:
    root = Path(cache_root) if cache_root is not None else analysis_cache_dir()
    src = root / "analysis" / session_id
    if not src.is_dir():
        return
    dest = staging / "analysis"
    shutil.copytree(src, dest, symlinks=True, dirs_exist_ok=True)
    _write_analysis_markdown_reports(dest)


def _collect_flags(session_dir: Path, staging: Path) -> None:
    """Write operator flags to outer ``flags.json`` when any exist.

    Flags are a groket annotation, not part of the official nested
    ``grok-trace.tar.gz`` (``grok trace`` does not pack them). Load from the
    session file or config-home fallback via :func:`~groket.flags.load_flags`.
    """
    from ..flags import load_flags

    flags = load_flags(session_dir)
    if not flags:
        return
    payload = [fl.model_dump() for fl in flags]
    (staging / "flags.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _collect_operator_notes(session_dir: Path, staging: Path) -> None:
    """Embed turn-linked operator notes under ``notes/``."""
    collect_notes_for_export(session_dir, staging / "notes")


def _write_readme(staging: Path, *, sid: str) -> None:
    text = (
        f"groket session export\n"
        f"=====================\n\n"
        f"session_id: {sid}\n\n"
        f"Always: {GROK_TRACE_ARCHIVE_NAME}, README.txt, manifest.json.\n"
        f"Optional when present: run/, analysis/, flags.json, notes/.\n\n"
        f"Contents\n"
        f"--------\n"
        f"{GROK_TRACE_ARCHIVE_NAME}\n"
        f"                 Nested archive from: grok trace --local {sid}\n"
        f"                 (exact CLI bytes). Grok session files only — not\n"
        f"                 groket flags/notes/analysis/run extras.\n\n"
        f"run/             Eval launch artifacts when session is under a work\n"
        f"                 volume (run.json, prompt, config, turn gate).\n"
        f"analysis/        Cached analysis results (*.json) + markdown (*.md).\n"
        f"flags.json       Operator flags (session or ~/.groket/flags fallback).\n"
        f"notes/           operator_notes.toml when notes exist.\n"
        f"                 Schema: ~/.groket/notes_schema.toml (not bundled).\n"
        f"manifest.json    Inventory of this outer bundle.\n\n"
        f"To recover the pure grok-trace archive::\n"
        f"  tar -xzf <this-bundle>.tar.gz {GROK_TRACE_ARCHIVE_NAME}\n"
    )
    (staging / "README.txt").write_text(text, encoding="utf-8")


def _assert_outer_layout(arcnames: list[str], *, sid: str) -> None:
    """Fail if the outer archive drifts from the nested-grok-trace contract."""
    if GROK_TRACE_ARCHIVE_NAME not in arcnames:
        raise RuntimeError(f"export bundle missing {GROK_TRACE_ARCHIVE_NAME}")
    nested_tars = [n for n in arcnames if n.endswith(".tar.gz")]
    if nested_tars != [GROK_TRACE_ARCHIVE_NAME]:
        raise RuntimeError(f"export must embed only {GROK_TRACE_ARCHIVE_NAME}, got {nested_tars}")
    if any(n == sid or n.startswith(f"{sid}/") for n in arcnames):
        raise RuntimeError(
            f"session files must live inside {GROK_TRACE_ARCHIVE_NAME}, not outer {sid}/"
        )


def export_session_bundle(
    session_dir: Path,
    *,
    dest: Path | None = None,
    analysis_cache_root: Path | None = None,
) -> ExportBundleResult:
    """Build an outer report tarball for *session_dir*.

    Always embeds :data:`GROK_TRACE_ARCHIVE_NAME` as the exact output of
    ``grok trace --local``. Optional ``run/`` and ``analysis/`` siblings hold
    eval-only extras (analysis includes JSON cache plus markdown reports).
    Fails hard if the Grok CLI is unavailable.

    :param session_dir: Grok session directory (…/%2Fworkspace/<session_id>/).
    :param dest: Outer ``.tar.gz`` path; default under :func:`~groket.paths.reports_dir`.
    :param analysis_cache_root: Override analysis cache root (tests).
    :returns: :class:`ExportBundleResult` with the path written.
    :raises FileNotFoundError: Session directory missing.
    :raises RuntimeError: ``grok`` missing, CLI export failed, or archive invalid.
    """
    session_dir = Path(session_dir).expanduser().resolve()
    if not session_dir.is_dir():
        raise FileNotFoundError(f"session directory not found: {session_dir}")
    sid = _session_id(session_dir)
    out = Path(dest) if dest is not None else default_bundle_path(sid)
    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="groket-bundle-") as tmp:
        staging = Path(tmp)
        nested = staging / GROK_TRACE_ARCHIVE_NAME
        build_grok_trace_archive(session_dir, nested)
        nested_members = assert_grok_trace_archive_shape(nested, sid)

        run_vol = run_volume_for_session(session_dir)
        if run_vol is not None:
            try:
                _collect_run_volume_files(run_vol, staging)
            except OSError:
                logger.warning("Failed to collect run volume files from %s", run_vol, exc_info=True)

        try:
            _collect_analysis(sid, staging, analysis_cache_root)
        except OSError:
            logger.debug("analysis cache collect failed", exc_info=True)
        try:
            _collect_flags(session_dir, staging)
        except OSError:
            logger.debug("flags collect failed", exc_info=True)
        try:
            _collect_operator_notes(session_dir, staging)
        except OSError:
            logger.debug("operator notes collect failed", exc_info=True)

        _write_readme(staging, sid=sid)

        skip_pack = frozenset({"bundle.tar.gz"})
        # Placeholder so ``manifest.json`` exists when we inventory members.
        (staging / "manifest.json").write_text("{}\n", encoding="utf-8")
        members = _staging_member_paths(staging, skip=skip_pack)
        if GROK_TRACE_ARCHIVE_NAME not in members:
            raise RuntimeError(f"export missing nested {GROK_TRACE_ARCHIVE_NAME}")

        manifest: JsonObject = as_json_object(
            {
                "schema": _MANIFEST_SCHEMA,
                "kind": "groket-session-export",
                "session_id": sid,
                "exported_at": datetime.now(UTC).isoformat(),
                "grok_trace": GROK_TRACE_ARCHIVE_NAME,
                "grok_trace_members": nested_members,
                "members": members,
            }
        )
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

        arcnames: list[str] = []
        tmp_out = staging / "bundle.tar.gz"
        with tarfile.open(tmp_out, "w:gz") as tf:
            for path in sorted(staging.iterdir()):
                if path.name == "bundle.tar.gz":
                    continue
                if path.is_file():
                    tf.add(path, arcname=path.name)
                    arcnames.append(path.name)
                elif path.is_dir():
                    arcnames.extend(_add_tree(tf, path, path.name))

        _assert_outer_layout(arcnames, sid=sid)

        try:
            shutil.move(str(tmp_out), str(out))
        except OSError as exc:
            raise RuntimeError(f"failed to write export bundle: {out}: {exc}") from exc

    return ExportBundleResult(
        path=out.resolve(),
        session_id=sid,
        arcnames=arcnames,
    )


__all__ = [
    "GROK_TRACE_ARCHIVE_NAME",
    "ExportBundleResult",
    "assert_grok_trace_archive_shape",
    "build_grok_trace_archive",
    "default_bundle_path",
    "export_session_bundle",
    "grok_trace_member_paths",
    "run_volume_for_session",
]

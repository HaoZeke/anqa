"""CLI entry point for groket — Typer (Click) app.

Default command launches the interactive TUI. Work/traces paths are fixed for
the process (CLI arguments only; not changed inside the TUI).

We intentionally use Typer/Click (not stdlib argparse alone) for Rich help,
nested ``gen`` (alias ``generator``) commands, and shell completion. Heavier CLI deps are acceptable.

Shell completion::

    uv run groket --install-completion
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from .ui.i18n import setup_i18n

setup_i18n()

app = typer.Typer(
    name="groket",
    help=(
        "Grok session trace evaluation — interactive TUI (default) and tools.\n\n"
        "Work and traces paths are set on the command line only; restart to change."
    ),
    no_args_is_help=False,
    add_completion=True,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
    epilog=(
        "Environment: GROKET_WORK_DIR (default work root), GROKET_LANG / LANG (Fluent UI language)."
    ),
)

gen_app = typer.Typer(
    name="gen",
    help=(
        "Generate user extensions under ~/.groket/ "
        "(detectors, rules, analysis plugins, tasks). "
        "Also accepts the name [bold]generator[/bold] "
        "(e.g. [cyan]groket generator detector …[/cyan])."
    ),
    no_args_is_help=True,
)
# Single registration — ``generator`` is rewritten to ``gen`` in :func:`main`
# so help does not list the command twice.
app.add_typer(gen_app, name="gen")


def launch_tui(
    path: Path | None,
    work_dir: Path | None,
    config: Path | None,
) -> None:
    from .paths import resolve_work_and_traces
    from .ui.app import TraceEvalApp

    traces_path = path.expanduser() if path is not None else None
    wd_arg = work_dir.expanduser() if work_dir is not None else None
    cfg = config.expanduser() if config is not None else None

    if traces_path is not None and wd_arg is None:
        wd, tr = resolve_work_and_traces(traces_path)
        typer.echo(f"groket: work_dir={wd}", err=True)
        typer.echo(f"  traces: {tr}", err=True)
        typer.echo(f"  runner writes: {wd / 'runs' / 'traces'}", err=True)
        TraceEvalApp(traces_path=tr, work_dir=wd, config_path=cfg).run()
    elif wd_arg is not None:
        typer.echo(f"groket: work_dir={wd_arg.resolve()}", err=True)
        typer.echo(
            f"  runner writes: {wd_arg.resolve() / 'runs' / 'traces'}",
            err=True,
        )
        TraceEvalApp(traces_path=traces_path, work_dir=wd_arg, config_path=cfg).run()
    else:
        TraceEvalApp(traces_path=traces_path, work_dir=wd_arg, config_path=cfg).run()


# Subcommand names — must not be consumed as a TUI path positional.
TOOL_COMMANDS = frozenset(
    {"batch", "audit", "refresh", "doctor-traces", "gen", "generator", "tasks"}
)

tasks_app = typer.Typer(
    name="tasks",
    help="Validate and describe batch tasks.yaml files (JSON Schema).",
    no_args_is_help=True,
)
app.add_typer(tasks_app, name="tasks")
# Shown once in help; argv alias only (see :func:`main`).
COMMAND_ALIASES = {"generator": "gen"}


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    path: Annotated[
        Path | None,
        typer.Option(
            "-P",
            "--path",
            help="Work root or traces directory for the TUI (or pass as first arg).",
            show_default=False,
        ),
    ] = None,
    work_dir: Annotated[
        Path | None,
        typer.Option(
            "-w",
            "--work-dir",
            help="Work root (Runner writes DIR/runs/traces).",
            show_default=False,
        ),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option(
            "-c",
            "--config",
            help="Path to config.json (default: ~/.groket/config.json).",
            show_default=False,
        ),
    ] = None,
) -> None:
    """Start the TUI when no subcommand is given."""
    if ctx.invoked_subcommand is not None:
        return
    launch_tui(path=path, work_dir=work_dir, config=config)


@app.command("batch")
def cmd_batch(
    tasks: Annotated[
        Path,
        typer.Option(
            "-t",
            "--tasks",
            exists=False,
            dir_okay=False,
            help="Tasks YAML file (required; no built-in catalog).",
        ),
    ],
    category: Annotated[
        str | None,
        typer.Option(
            "-C",
            "--category",
            help="Only tasks of this category (regular|adversarial).",
        ),
    ] = None,
    task_id: Annotated[
        list[str] | None,
        typer.Option("-i", "--task-id", help="Only these task ids (repeatable)."),
    ] = None,
    models: Annotated[
        list[str] | None,
        typer.Option("-m", "--models", help="Model ids (default: host catalog)."),
    ] = None,
    parallelism: Annotated[
        int,
        typer.Option("-p", "--parallelism", min=1, help="Concurrent tasks."),
    ] = 1,
) -> None:
    """Run eval tasks through Docker."""
    from .runs.batch import load_models, load_tasks, run_batch

    if category is not None and category not in ("regular", "adversarial"):
        typer.echo("error: --category must be regular or adversarial", err=True)
        raise typer.Exit(2)
    try:
        loaded = load_tasks(tasks, category)
    except FileNotFoundError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    if task_id:
        loaded = [t for t in loaded if t.task_id in task_id]
    if not loaded:
        typer.echo("No tasks matched.")
        raise typer.Exit(0)
    run_batch(
        loaded,
        models=models or load_models(),
        parallelism=parallelism,
    )


@app.command("audit")
def cmd_audit(
    traces_dir: Annotated[
        Path,
        typer.Argument(
            help="Traces directory.",
        ),
    ] = Path.home() / "groket" / "runs" / "traces",
    output: Annotated[
        Path | None,
        typer.Option("-o", "--output", help="Save report to this file."),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option("-c", "--config", help="Path to config.json."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("-j", "--json", help="Emit JSON instead of Markdown."),
    ] = False,
) -> None:
    """Run analysis plugins over traces; print or save a report."""
    from .analysis import AnalysisService
    from .parser import find_sessions
    from .paths import analysis_cache_dir, default_work_dir

    svc = AnalysisService(
        default_work_dir(),
        config_path=config,
        cache_root=analysis_cache_dir(),
    )
    sessions = find_sessions(traces_dir)
    from .models import JsonObject, json_as_str

    all_results: dict[str, list[JsonObject]] = {}
    total_findings = 0
    total_high = 0
    for sd in sessions:
        results = svc.analyze_all(sd)
        session_findings: list[JsonObject] = []
        for _aid, r in results.items():
            for finding in r.findings:
                row: JsonObject = {
                    "id": finding.id,
                    "plugin_id": finding.plugin_id,
                    "severity": finding.severity.value,
                    "title": finding.title,
                    "detail": finding.detail,
                    "category": finding.category,
                }
                session_findings.append(row)
                total_findings += 1
                if finding.severity.value == "high":
                    total_high += 1
        if session_findings:
            all_results[sd.name] = session_findings

    if as_json:
        text = json.dumps(all_results, indent=2, default=str)
    else:
        lines = [
            f"# Audit: {traces_dir}",
            f"Sessions: {len(sessions)}",
            f"Findings: {total_findings} ({total_high} high)",
            "",
        ]
        for sid, findings in all_results.items():
            lines.append(f"## {sid}")
            for row in findings:
                sev = json_as_str(row.get("severity"))
                plugin = json_as_str(row.get("plugin_id"))
                title = json_as_str(row.get("title"))
                lines.append(f"  - [{sev}] [{plugin}] {title}")
                detail = row.get("detail")
                if detail:
                    lines.append(f"    {json_as_str(detail)[:200]}")
            lines.append("")
        text = "\n".join(lines)

    if output is not None:
        output.write_text(text, encoding="utf-8")
        typer.echo(f"Report saved to {output}")
    else:
        typer.echo(text)


@app.command("self-test")
def cmd_self_test(
    work_dir: Annotated[
        Path | None,
        typer.Option("-w", "--work-dir", help="Work root (default ~/groket)."),
    ] = None,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of text lines."),
    ] = False,
) -> None:
    """Check Docker, Grok auth, work dir, and related host dependencies."""
    from .diagnostics import run_self_test
    from .paths import default_work_dir

    wd = work_dir.expanduser() if work_dir else default_work_dir()
    report = run_self_test(work_dir=wd)
    if json_out:
        payload = {
            "ok": report.ok,
            "fail_count": report.fail_count,
            "warn_count": report.warn_count,
            "checks": [
                {
                    "id": c.id,
                    "name": c.name,
                    "ok": c.ok,
                    "required": c.required,
                    "detail": c.detail,
                    "level": c.level,
                }
                for c in report.checks
            ],
        }
        typer.echo(json.dumps(payload, indent=2))
    else:
        for line in report.lines():
            typer.echo(line)
    raise typer.Exit(0 if report.ok else 1)


@app.command("refresh")
def cmd_refresh(
    work_dir: Annotated[
        Path | None,
        typer.Option("-w", "--work-dir", help="Work root (default ~/groket)."),
    ] = None,
    traces: Annotated[
        Path | None,
        typer.Option("-T", "--traces", help="Override traces root."),
    ] = None,
    recent: Annotated[
        int,
        typer.Option(
            "-n",
            "--recent",
            help="Only newest N sessions by mtime (0=all).",
        ),
    ] = 0,
    limit: Annotated[
        int,
        typer.Option("-l", "--limit", help="Cap sessions processed (0=unlimited)."),
    ] = 0,
    config: Annotated[
        Path | None,
        typer.Option("-c", "--config", help="Path to config.json."),
    ] = None,
    quiet: Annotated[bool, typer.Option("-q", "--quiet")] = False,
) -> None:
    """Rescan traces and run all analysis plugins."""
    from .analysis import AnalysisService
    from .parser import find_sessions, load_session_meta
    from .paths import analysis_cache_dir, default_work_dir

    wd = work_dir.expanduser() if work_dir else default_work_dir()
    traces_root = traces.expanduser() if traces else wd / "runs" / "traces"

    if not quiet:
        typer.echo(f"refresh: work_dir={wd}\n  traces={traces_root}", err=True)

    sessions = list(find_sessions(traces_root))

    def _mtime(sd: Path) -> float:
        for name in ("summary.json", "events.jsonl", "updates.jsonl"):
            fp = sd / name
            if fp.exists():
                try:
                    return fp.stat().st_mtime
                except OSError:
                    pass
        try:
            return sd.stat().st_mtime
        except OSError:
            return 0.0

    sessions.sort(key=_mtime, reverse=True)
    if recent:
        sessions = sessions[:recent]
    if limit:
        sessions = sessions[:limit]

    svc = AnalysisService(wd, config_path=config, cache_root=analysis_cache_dir())
    ok = err = 0
    plugins = [p for p in svc.list_plugins() if p.id != "noop"]
    if not quiet:
        names = ", ".join(p.id for p in plugins) or "(none)"
        typer.echo(
            f"refresh: {len(plugins)} plugin(s) [{names}] on {len(sessions)} session(s)…",
            err=True,
        )
    for i, sd in enumerate(sessions, 1):
        try:
            load_session_meta(sd)
            results = svc.analyze_all(sd)
            if all(r.ok for r in results.values()):
                ok += 1
            else:
                err += 1
                errs = [f"{k}: {r.error}" for k, r in results.items() if not r.ok]
                if not quiet and errs:
                    typer.echo(
                        f"  [{i}] err {sd.name}: {'; '.join(errs)}",
                        err=True,
                    )
        except Exception as exc:
            err += 1
            if not quiet:
                typer.echo(f"  [{i}] err {sd.name}: {exc}", err=True)

    typer.echo(f"Refresh done: sessions={len(sessions)} plugins={len(plugins)} ok={ok} err={err}")


@app.command("doctor-traces")
def cmd_doctor_traces(
    traces_dir: Annotated[
        Path | None,
        typer.Argument(help="Traces root (default: <work-dir>/runs/traces)."),
    ] = None,
    work_dir: Annotated[
        Path | None,
        typer.Option("-w", "--work-dir"),
    ] = None,
    mark: Annotated[
        bool,
        typer.Option(
            "-m",
            "--mark",
            help="Write groket-interrupted.json on incomplete sessions.",
        ),
    ] = False,
    prune_shells: Annotated[
        bool,
        typer.Option(
            "-s",
            "--prune-shells",
            help="Remove empty eval run folders.",
        ),
    ] = False,
    prune_docker: Annotated[
        bool,
        typer.Option(
            "-D",
            "--prune-docker",
            help="Remove exited eval containers.",
        ),
    ] = False,
    kill_stale: Annotated[
        bool,
        typer.Option(
            "-k",
            "--kill-stale",
            help="Also stop+remove still-running eval containers.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "-n",
            "--dry-run",
            help="Report only; do not write/delete.",
        ),
    ] = False,
) -> None:
    """Find interrupted traces; optionally prune shells / containers."""
    from .paths import default_traces_root, default_work_dir
    from .runs.run_configs import (
        audit_trace_sessions,
        mark_interrupted_sessions,
        prune_orphan_trace_runs,
    )

    wd = work_dir.expanduser() if work_dir else default_work_dir()
    traces_root = traces_dir.expanduser() if traces_dir else default_traces_root(wd)

    if mark:
        stats = mark_interrupted_sessions(traces_root, dry_run=dry_run)
    else:
        stats = audit_trace_sessions(traces_root)

    from .models import ParamBag, json_as_object, json_as_str_list

    st = ParamBag({str(k): v for k, v in stats.items()})
    typer.echo(f"traces_root: {st.as_str('traces_root', str(traces_root))}")
    typer.echo(f"ok_sessions: {st.as_int('ok_count')}")
    typer.echo(f"running_in_progress: {st.as_int('running_count')}")
    typer.echo(f"interrupted_or_incomplete: {st.as_int('interrupted_count')}")
    typer.echo(f"empty_shell_runs: {st.as_int('empty_shell_count')}")
    if mark:
        typer.echo(f"marked: {st.as_int('marked_count')} (dry_run={dry_run})")
    running_raw = st.get("running")
    running_items = running_raw if isinstance(running_raw, list) else []
    for item in running_items[:20]:
        row = ParamBag(json_as_object(item if isinstance(item, dict) else None))
        typer.echo(
            f"  [running] {row.as_str('session_id')}  age_s={row.as_str('trace_age_s', '?')}  "
            f"{row.as_str('session_dir')}"
        )
    interrupted_raw = st.get("interrupted")
    interrupted_items = interrupted_raw if isinstance(interrupted_raw, list) else []
    for item in interrupted_items[:40]:
        row = ParamBag(json_as_object(item if isinstance(item, dict) else None))
        typer.echo(
            f"  [{row.as_str('status')}] {row.as_str('session_id')}  "
            f"outcome={row.as_str('turn_outcome') or 'NONE'}  {row.as_str('session_dir')}"
        )
    extra = len(interrupted_items) - 40
    if extra > 0:
        typer.echo(f"  … and {extra} more")
    for sh in json_as_str_list(st.get("empty_shells"))[:20]:
        typer.echo(f"  [shell] {sh}")

    if prune_shells:
        pr = prune_orphan_trace_runs(traces_root, dry_run=dry_run)
        typer.echo(
            f"prune_shells: removed={pr.get('removed_count', 0)} "
            f"kept={pr.get('kept', 0)} dry_run={dry_run}"
        )

    if prune_docker or kill_stale:
        try:
            from .docker.orchestrator import DockerOrchestrator

            orch = DockerOrchestrator(wd)
            dstats = orch.prune_eval_containers(
                remove_exited=True,
                remove_running=bool(kill_stale),
            )
            typer.echo(
                f"prune_docker: exited_removed={dstats.get('exited_removed', 0)} "
                f"running_removed={dstats.get('running_removed', 0)} "
                f"kill_stale={bool(kill_stale)}"
            )
        except Exception as exc:
            typer.echo(f"prune_docker failed: {exc}", err=True)

    errs_raw = stats.get("errors")
    errs_list = errs_raw if isinstance(errs_raw, list) else []
    if errs_list:
        for e in errs_list:
            typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)


@gen_app.command("detector")
def gen_detector(
    name: Annotated[str, typer.Argument(help="Detector name / file stem.")],
    force: Annotated[
        bool,
        typer.Option("-f", "--force", help="Overwrite if exists."),
    ] = False,
) -> None:
    """Create ~/.groket/detectors/<name>.py with @detector stub."""
    from .extensions.scaffold import slug_name, write_detector
    from .paths import ensure_user_extension_dirs

    ensure_user_extension_dirs()
    try:
        path = write_detector(name, force=force)
    except FileExistsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Wrote detector module: {path}")
    typer.echo(f"  @detector name: {slug_name(name).replace('-', '_')}")
    typer.echo("  Pair with: uv run groket gen rule <id> --detector <name>")


@gen_app.command("rule")
def gen_rule(
    rule_id: Annotated[str, typer.Argument(help="Rule id (e.g. my-custom-rule).")],
    detector: Annotated[
        str,
        typer.Option(
            "-d",
            "--detector",
            help="Detector name (default: from rule id).",
        ),
    ] = "",
    force: Annotated[bool, typer.Option("-f", "--force")] = False,
) -> None:
    """Create ~/.groket/rules/<id>.yaml merged with bundled rules."""
    from .extensions.scaffold import slug_name, write_rule
    from .paths import ensure_user_extension_dirs

    ensure_user_extension_dirs()
    det = detector or slug_name(rule_id).replace("-", "_")
    try:
        path = write_rule(rule_id, detector=det, force=force)
    except FileExistsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Wrote rule YAML: {path}")
    typer.echo(f"  detector: {det}")


@gen_app.command("plugin")
def gen_plugin(
    name: Annotated[str, typer.Argument(help="Module stem (e.g. my_session_stats).")],
    register: Annotated[
        bool,
        typer.Option(
            "-r",
            "--register",
            help="Append module:ClassName to ~/.groket/config.json analysis.plugins.",
        ),
    ] = False,
    force: Annotated[bool, typer.Option("-f", "--force")] = False,
) -> None:
    """Create ~/.groket/plugins/<name>.py analysis Analyzer class."""
    from .extensions.scaffold import (
        append_analysis_plugin_to_config,
        slug_name,
        snake_to_pascal,
        write_analysis_plugin,
    )
    from .paths import ensure_user_extension_dirs

    ensure_user_extension_dirs()
    try:
        path = write_analysis_plugin(name, force=force)
    except FileExistsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    stem = slug_name(name).replace("-", "_")
    cls = snake_to_pascal(stem) + "Analyzer"
    typer.echo(f"Wrote analysis plugin: {path}")
    typer.echo(f"  config entry: {stem}:{cls}")
    if register:
        cfg = append_analysis_plugin_to_config(stem, cls)
        typer.echo(f"  updated {cfg}")
    else:
        typer.echo(f'  enable with analysis.plugins: ["{stem}:{cls}"] or pass --register')


@gen_app.command("tasks")
def gen_tasks(
    path: Annotated[
        Path | None,
        typer.Argument(help="Output path (default: ~/.groket/tasks/example_tasks.yaml)."),
    ] = None,
    force: Annotated[bool, typer.Option("-f", "--force")] = False,
) -> None:
    """Create a tasks YAML for ``groket batch --tasks``."""
    from .extensions.scaffold import write_tasks_file
    from .paths import ensure_user_extension_dirs

    ensure_user_extension_dirs()
    try:
        out = write_tasks_file(path, force=force)
    except FileExistsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Wrote tasks file: {out}")
    typer.echo(f"  run: uv run groket batch --tasks {out}")
    typer.echo("  validate: uv run groket tasks validate <path>")


@tasks_app.command("validate")
def tasks_validate(
    path: Annotated[
        Path,
        typer.Argument(help="Tasks YAML file to validate."),
    ],
) -> None:
    """Validate a tasks.yaml against the Pydantic task schema."""
    from pydantic import ValidationError

    from .runs.task_schema import validate_tasks_path

    try:
        doc = validate_tasks_path(path)
    except FileNotFoundError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    except ValidationError as exc:
        typer.echo(exc, err=True)
        raise typer.Exit(1) from exc
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"ok: {path} ({len(doc.tasks)} task(s), schema_version={doc.schema_version})")


@tasks_app.command("schema")
def tasks_schema(
    out: Annotated[
        Path | None,
        typer.Option("-o", "--out", help="Write JSON Schema to this path (default: stdout)."),
    ] = None,
) -> None:
    """Emit JSON Schema for tasks.yaml (for editors and CI)."""
    from .runs.task_schema import emit_tasks_schema

    text = emit_tasks_schema(out)
    if out is None:
        typer.echo(text, nl=False)
    else:
        typer.echo(f"Wrote {out}")


def main(argv: list[str] | None = None) -> None:
    """Console script entry (``groket = groket.cli:main``)."""
    args = list(sys.argv[1:] if argv is None else argv)

    # Command aliases (one help entry; rewrite before Typer sees argv).
    if args and args[0] in COMMAND_ALIASES:
        args = [COMMAND_ALIASES[args[0]], *args[1:]]

    # Leading path positional for TUI only (so ``batch`` is never eaten as PATH).
    # ``groket ./project -w …`` → ``groket -P ./project -w …``
    if args and not args[0].startswith("-") and args[0] not in TOOL_COMMANDS:
        args = ["-P", args[0], *args[1:]]

    app(args=args, prog_name="groket")


if __name__ == "__main__":
    main()

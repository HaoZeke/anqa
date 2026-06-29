"""Rich renderables for trace event / tool detail (ported from tools/trace_viewer.py)."""

from __future__ import annotations

import json
import logging
import re
from contextlib import suppress
from pathlib import Path

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.rule import Rule
from rich.syntax import Syntax
from rich.text import Text
from textual.app import App

from ..analysis.base import Finding
from ..models import Flag, JsonObject, JsonValue, ToolInputBag, TraceEvent
from ..utils import fmt_duration
from .i18n import t
from .styles import severity_style

logger = logging.getLogger(__name__)
_RE_ANSI_CSI = re.compile(t("ui-x1b"))
_RE_ANSI_OSC = re.compile(t("ui-x1b-x07-x1b"))
_RE_ANSI_ESC = re.compile("\\x1b[@-Z\\\\-_]")
_RE_C0_NOISE = re.compile("[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f]")
_RE_CR = re.compile(t("ui-r-n"))
_RE_REPEATED_FFFD = re.compile(t("ui-ufffd-2"))


def sanitize_console_text(text: str, *, for_display: bool = True) -> str:
    """Strip ANSI / control characters from terminal-like tool output.

    Preserves normal newlines and tabs; normalizes CR/CRLF; collapses spinner
    overwrite lines into readable plain text suitable for the detail pane.
    """
    if not text:
        return ""
    if not isinstance(text, str):
        text = str(text)
    s = text
    s = s.replace("\r\n", "\n")
    s = _RE_ANSI_OSC.sub("", s)
    s = _RE_ANSI_CSI.sub("", s)
    s = _RE_ANSI_ESC.sub("", s)
    s = s.replace("\x1b", "")
    s = s.replace("\r", "\n")
    s = _RE_C0_NOISE.sub("", s)
    s = _RE_REPEATED_FFFD.sub("", s)
    if for_display:
        lines_out: list[str] = []
        for ln in s.split("\n"):
            t = ln.rstrip()
            if not t:
                if lines_out and lines_out[-1] == "":
                    continue
                lines_out.append("")
                continue
            printable = sum(1 for ch in t if ch.isprintable() or ch in "\t")
            if printable < max(1, len(t) // 4) and len(t) > 4:
                continue
            lines_out.append(t)
        s = "\n".join(lines_out)
        s = re.sub(r"\n{4,}", "\n\n\n", s)
    return s


def _looks_like_console_output(text: str, tool_name: str = "") -> bool:
    """Heuristic: treat as terminal stream (aggressive sanitize + text lexer)."""
    if tool_name in (
        "run_terminal_command",
        "get_command_or_subagent_output",
        "monitor",
        "wait_commands_or_subagents",
    ):
        return True
    if not text:
        return False
    sample = text[:4000]
    if "\x1b[" in sample or "\x1b]" in sample or "\r" in sample:
        return True
    noisy = sum(1 for ch in sample if ord(ch) < 32 and ch not in "\t\n")
    return noisy > 8


from .styles import EVENT_TYPE_STYLE as KIND_STYLES
from .styles import SYNTAX_THEME_DARK, syntax_theme_for_app, tool_style

_EXT_LANG = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".json": "json",
    ".md": "markdown",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".css": "css",
    ".html": "html",
    ".xml": "xml",
    ".sql": "sql",
    ".rb": "ruby",
    ".diff": "diff",
    ".patch": "diff",
}


def set_static_renderable(widget, renderable: RenderableType) -> None:
    """Update a Static with Markdown/Group/Text safely."""
    try:
        widget.update(renderable)
    except Exception:
        logger.debug(t("ui-failed-to-update-widget-with-renderable"), exc_info=True)
        widget.update(Text(str(renderable)))


def _syntax(code: str, lexer: str, line_numbers: bool = False, *, app: App | None = None) -> Syntax:
    theme = syntax_theme_for_app(app) if app is not None else SYNTAX_THEME_DARK
    return Syntax(
        code or "",
        lexer,
        theme=theme,
        line_numbers=line_numbers,
        word_wrap=True,
        background_color="default",
    )


def _looks_json(s: str) -> bool:
    s = (s or "").lstrip()
    return bool(s) and s[0] in "{[" and (s.rstrip()[-1:] in "}]")


def _looks_diff(s: str) -> bool:
    if not s:
        return False
    hits = sum(
        1
        for ln in s.splitlines()[:40]
        if ln[:1] in "+-" or ln.startswith(("@@", t("ui-diff"), "--- ", "+++ "))
    )
    return hits >= 3


def _lang_from_path(path: str) -> str:
    p = (path or "").lower().split("?")[0]
    if p.rsplit("/", 1)[-1] == "dockerfile":
        return "dockerfile"
    return _EXT_LANG.get(Path(p).suffix, "")


def _path_hint(ri: dict) -> str:
    for k in ("target_file", "file_path", "path", "target_directory"):
        v = ri.get(k)
        if isinstance(v, str) and v:
            return v
    return ""


def _guess_lexer(text: str, tool_name: str = "", path_hint: str = "") -> str:
    if path_hint:
        lang = _lang_from_path(path_hint)
        if lang:
            return lang
    if _looks_json(text):
        return "json"
    if _looks_diff(text):
        return "diff"
    if tool_name == "run_terminal_command":
        return "bash"
    head = (text or "").lstrip()
    if head.startswith("#!"):
        return "bash"
    if head.startswith("<?xml") or head.startswith("<!DOCTYPE"):
        return "xml"
    return "text"


def _content_str(
    content: str | list[JsonValue] | JsonObject | None,
    *,
    sanitize: bool = False,
    tool_name: str = "",
) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        s = content
    else:
        try:
            s = json.dumps(content, indent=2, ensure_ascii=False)
        except Exception:
            s = str(content)
    if sanitize or _looks_like_console_output(s, tool_name):
        s = sanitize_console_text(s)
    return s


def _truncate_mid(s: str, head: int = 7000, tail: int = 5000, limit: int = 14000) -> str:
    if len(s) <= limit:
        return s
    return s[:head] + t("ui-truncated") + s[-tail:]


def _render_tool_input(tname: str, ri: dict) -> list:
    """Syntax-highlighted tool input sections (trace_viewer render_tool_detail)."""
    parts: list = []
    path_hint = _path_hint(ri)
    if tname == "run_terminal_command" and "command" in ri:
        parts.append(_syntax(str(ri.get("command") or ""), "bash"))
        extra = {k: v for k, v in ri.items() if k != "command"}
        if extra:
            with suppress(Exception):
                parts.append(_syntax(json.dumps(extra, indent=2, ensure_ascii=False), "json"))
        return parts
    if tname == "search_replace":
        fp = ri.get("file_path") or ri.get("target_file") or ""
        if fp:
            parts.append(Text(f"{t('ui-file')} {fp}", style="cyan"))
        lang = _lang_from_path(str(fp)) or "text"
        old_s, new_s = (str(ri.get("old_string") or ""), str(ri.get("new_string") or ""))
        if old_s:
            parts.append(Text("old_string:", style="red"))
            parts.append(_syntax(old_s[:8000], lang, line_numbers=True))
        if new_s:
            parts.append(Text("new_string:", style="green"))
            parts.append(_syntax(new_s[:8000], lang, line_numbers=True))
        extra = {
            k: v
            for k, v in ri.items()
            if k not in ("file_path", "target_file", "old_string", "new_string")
        }
        if extra:
            with suppress(Exception):
                parts.append(_syntax(json.dumps(extra, indent=2, ensure_ascii=False), "json"))
        return parts
    if tname == "read_file":
        tf = ri.get("target_file") or ri.get("file_path") or path_hint
        if tf:
            parts.append(Text(f"{t('ui-target-file')} {tf}", style="cyan"))
        extra = {k: v for k, v in ri.items() if k not in ("target_file", "file_path")}
        if extra:
            with suppress(Exception):
                parts.append(_syntax(json.dumps(extra, indent=2, ensure_ascii=False), "json"))
        elif not tf:
            try:
                parts.append(_syntax(json.dumps(ri, indent=2, ensure_ascii=False), "json"))
            except Exception:
                parts.append(Text(str(ri)))
        return parts
    if tname == "grep":
        pat = ri.get("pattern")
        if pat is not None:
            parts.append(Text("pattern:", style="magenta"))
            parts.append(_syntax(str(pat), "text"))
        extra = {k: v for k, v in ri.items() if k != "pattern"}
        if extra:
            with suppress(Exception):
                parts.append(_syntax(json.dumps(extra, indent=2, ensure_ascii=False), "json"))
        return parts
    if tname == "list_dir":
        td = ri.get("target_directory") or path_hint
        if td:
            parts.append(Text(f"{t('ui-target-directory')} {td}", style="blue"))
        extra = {k: v for k, v in ri.items() if k != "target_directory"}
        if extra:
            with suppress(Exception):
                parts.append(_syntax(json.dumps(extra, indent=2, ensure_ascii=False), "json"))
        return parts
    if tname == "todo_write":
        try:
            parts.append(_syntax(json.dumps(ri, indent=2, ensure_ascii=False), "json"))
        except Exception:
            parts.append(Text(str(ri)))
        return parts
    if tname in ("web_search", "spawn_subagent", "ask_user_question"):
        for key in ("query", "prompt", "description", "question"):
            if key in ri and isinstance(ri[key], str) and ri[key].strip():
                parts.append(Text(f"{key}:", style="bright_blue"))
                val = ri[key]
                if len(val) > 4000:
                    val = val[:4000] + t("ui-truncated-1")
                if key in ("prompt", "description") and "\n" in val:
                    parts.append(Markdown(val))
                else:
                    parts.append(Text(val))
        extra = {
            k: v
            for k, v in ri.items()
            if k not in ("query", "prompt", "description", "question") or not isinstance(v, str)
        }
        extra = {
            k: v
            for k, v in ri.items()
            if not (
                k in ("query", "prompt", "description", "question")
                and isinstance(v, str)
                and v.strip()
            )
        }
        if extra:
            with suppress(Exception):
                parts.append(_syntax(json.dumps(extra, indent=2, ensure_ascii=False), "json"))
        return parts
    try:
        inp_s = json.dumps(ri, indent=2, ensure_ascii=False)
    except Exception:
        inp_s = str(ri)
    parts.append(_syntax(inp_s, "json" if _looks_json(inp_s) else "text"))
    return parts


def _render_tool_output(out: str, tname: str, path_hint: str) -> list:
    """Syntax-highlighted tool output (trace_viewer output block)."""
    parts: list = []
    raw_len = len(out or "")
    cleaned = sanitize_console_text(out or "")
    if not cleaned and out:
        cleaned = sanitize_console_text(out, for_display=False) or t(
            "ui-binary-control-only-output"
        )
    n_out = len(cleaned)
    out_disp = _truncate_mid(cleaned)
    note = ""
    if raw_len and n_out < raw_len * 0.9:
        note = f"{t('ui-cleaned-from')} {raw_len}"
    parts.append(Rule(f"{t('ui-output')} {n_out} {t('ui-chars')} {note})", style="bright_black"))
    if not out_disp.strip():
        parts.append(Text("(empty)", style="dim italic"))
        return parts
    console_like = _looks_like_console_output(out or "", tname) or tname in (
        "run_terminal_command",
        "get_command_or_subagent_output",
        "monitor",
    )
    lexer = _guess_lexer(out_disp, tname, path_hint)
    if tname == "read_file" and path_hint:
        lexer = _lang_from_path(path_hint) or lexer
    if console_like and lexer == "bash" and (tname != "run_terminal_command"):
        lexer = "text"
    if console_like and tname == "run_terminal_command":
        lexer = "text"
    if lexer == "json" or _looks_json(out_disp):
        with suppress(Exception):
            out_disp = json.dumps(json.loads(out_disp), indent=2, ensure_ascii=False)
            lexer = "json"
    ln = (
        lexer in ("python", "javascript", "typescript", "rust", "go", "tsx", "jsx")
        and out_disp.count("\n") > 3
    )
    parts.append(_syntax(out_disp, lexer or "text", line_numbers=ln))
    return parts


def render_tool_detail(
    *,
    index: int,
    tool_name: str,
    raw_input: dict | None = None,
    output: str = "",
    is_error: bool = False,
    tool_call_id: str = "",
    exit_code: int | None = None,
    signal: str = "",
    time_str: str = "",
    update_index: int | None = None,
    event_type: str = "tool",
    duration: float | None = None,
) -> Group:
    """Unified tool detail (trace_viewer render_tool_detail), call+result merged."""
    ri = raw_input or {}
    path_hint = _path_hint(ri)
    tname = tool_name or "?"
    style = tool_style(tname)
    head = Text()
    head.append(f"#{index} ", style="dim")
    head.append(t("ui-tool"), style="dim")
    head.append(tname, style=style)
    if is_error:
        head.append(t("ui-error"), style="bold red")
    if event_type and event_type != "tool":
        head.append(f"  ({event_type})", style="dim")
    head.append("\n")
    meta = Text()
    meta_bits: list[str] = []
    if time_str:
        meta_bits.append(time_str)
    if duration is not None:
        meta_bits.append(fmt_duration(duration))
    if exit_code is not None:
        meta_bits.append(f"exit {exit_code}")
    if signal:
        meta_bits.append(f"signal {signal}")
    if meta_bits:
        meta.append("  ·  ".join(meta_bits), style="dim" if not is_error else "red")
        meta.append("\n")
    if path_hint:
        meta.append(path_hint, style="cyan")
        meta.append("\n")
    parts: list = [head, meta]
    if ri:
        parts += [Text(""), Rule(t("ui-input"), style="bright_black")]
        parts.extend(_render_tool_input(tname, ri))
    else:
        parts += [
            Text(""),
            Rule(t("ui-input"), style="bright_black"),
            Text(t("ui-no-input"), style="dim italic"),
        ]
    parts.append(Text(""))
    parts.extend(_render_tool_output(output, tname, path_hint))
    return Group(*parts)


def render_tool_detail_from_event(
    ev: TraceEvent,
    *,
    paired_call: TraceEvent | None = None,
    paired_result: TraceEvent | None = None,
    duration: float | None = None,
) -> Group:
    """Render tool_call / tool_result, merging pair when available (trace_viewer style)."""
    call = ev if ev.event_type == "tool_call" else paired_call
    result = ev if ev.event_type == "tool_result" else paired_result
    ri: dict[str, JsonValue] = {}
    src_ev = call if call is not None else ev
    bag = (
        src_ev.raw_input
        if isinstance(src_ev.raw_input, ToolInputBag)
        else ToolInputBag(src_ev.raw_input if isinstance(src_ev.raw_input, dict) else {})
    )
    ri = dict(bag.raw())
    tname = (
        (call.tool_name if call else "")
        or (result.tool_name if result else "")
        or ev.tool_name
        or "?"
    )
    out = ""
    if result:
        out = _content_str(result.content, sanitize=True, tool_name=tname)
    elif ev.event_type == "tool_call":
        out = _content_str(ev.content, sanitize=True, tool_name=tname)
    else:
        out = _content_str(ev.content, sanitize=True, tool_name=tname)
    is_err = bool(result and result.is_error or ev.is_error or (call and call.is_error))
    exit_code = None
    signal = ""
    for src in (result, call, ev):
        if src is None:
            continue
        ec = getattr(src, "exit_code", None)
        sig = getattr(src, "signal", None) or ""
        if exit_code is None and ec is not None:
            exit_code = ec
        if not signal and sig:
            signal = sig
        ri_src = getattr(src, "raw_input", None) or {}
        if exit_code is None and isinstance(ri_src, dict) and ("exit_code" in ri_src):
            try:
                exit_code = int(ri_src["exit_code"])
            except (TypeError, ValueError):
                pass
    idx = ev.index
    time_str = ev.time_str
    update_index = ev.update_index
    call_id = (
        ev.tool_call_id
        or (call.tool_call_id if call else "")
        or (result.tool_call_id if result else "")
    )
    return render_tool_detail(
        index=idx,
        tool_name=tname,
        raw_input=ri,
        output=out,
        is_error=is_err,
        tool_call_id=call_id,
        exit_code=exit_code,
        signal=signal,
        time_str=time_str,
        update_index=update_index,
        event_type=ev.event_type,
        duration=duration,
    )


def render_event_detail(
    ev: TraceEvent,
    *,
    finding: Finding | None = None,
    flag: Flag | None = None,
    duration: float | None = None,
    paired_call: TraceEvent | None = None,
    paired_result: TraceEvent | None = None,
) -> RenderableType:
    """Full detail pane for any TraceEvent (trace_viewer render_event_detail + banners)."""
    banners: list = []
    if flag:
        ft = Text()
        ft.append(t("ui-flagged"), style="red bold")
        ft.append(f"[{flag.verdict.value}] {flag.description}\n", style="red")
        ft.append(f"{t('ui-flagged-at')} {flag.created_at}", style="dim")
        banners.append(ft)
    if finding:
        sc = severity_style(finding.severity.value)
        it = Text()
        it.append(t("ui-finding"), style=f"{sc} bold")
        it.append(f"  [{finding.plugin_id}] {finding.category}: {finding.title}\n", style=sc)
        it.append(f"  {(finding.detail or '')[:400]}", style="dim")
        banners.append(it)
    if ev.event_type in ("tool_call", "tool_result"):
        core = render_tool_detail_from_event(
            ev, paired_call=paired_call, paired_result=paired_result, duration=duration
        )
        if banners:
            return Group(*banners, Text(""), core)
        return core
    style = KIND_STYLES.get(ev.event_type, "white")
    if ev.is_error and ev.event_type in ("session", "session_error"):
        style = "bold red"
    head = Text()
    head.append(f"#{ev.index} ", style="dim")
    head.append(ev.type_label or ev.event_type, style=style)
    if ev.is_error:
        head.append(t("ui-error-1"), style="bold red")
    head.append("\n")
    meta_parts: list[str] = []
    if ev.time_str:
        meta_parts.append(ev.time_str)
    if duration is not None:
        meta_parts.append(fmt_duration(duration))
    if meta_parts:
        head.append("  ·  ".join(meta_parts), style="dim")
    body = _content_str(ev.content, sanitize=True, tool_name=ev.tool_name or "")
    if len(body) > 20000:
        body = body[:10000] + t("ui-truncated") + body[-8000:]
    chunks: list = []
    if banners:
        chunks.extend(banners)
        chunks.append(Text(""))
    chunks.append(head)
    if ev.event_type in ("assistant", "user") and body.strip():
        chunks += [Text(""), Markdown(body)]
    elif ev.event_type == "thought" and body.strip():
        chunks += [
            Text(""),
            Rule(t("ui-thought"), style="bright_black"),
            Text(body, style="dim italic"),
        ]
    elif ev.event_type == "plan":
        chunks += [Text(""), Rule(t("ui-plan"), style="bright_black"), Text(body, style="magenta")]
    elif ev.event_type == "subagent" and body.strip():
        chunks += [
            Text(""),
            Rule(t("ui-subagent"), style="bright_black"),
            Markdown(body) if "#" in body[:200] or "\n" in body else Text(body, style="yellow"),
        ]
    elif ev.event_type in ("session", "session_error"):
        chunks += [
            Text(""),
            Text(body or "(empty)", style="bold red" if ev.is_error else "yellow"),
        ]
    elif body.strip():
        chunks += [Text(""), Text(body)]
    else:
        chunks += [Text(""), Text("(empty)", style="dim")]
    return Group(*chunks)


def render_markdown_doc(text: str, *, max_chars: int = 120000) -> RenderableType:
    """Markdown document for Summary / Feedback tabs."""
    body = text or "_empty_"
    if len(body) > max_chars:
        body = body[: max_chars // 2] + t("ui-truncated-for-display") + body[-(max_chars // 3) :]
    try:
        return Markdown(body)
    except Exception:
        return Text(body)

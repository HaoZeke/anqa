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

from .. import event_types as et
from ..analysis.base import Finding
from ..models import Flag, JsonObject, JsonValue, ToolInputBag, TraceEvent
from ..utils import fmt_duration
from .i18n import t
from .styles import severity_style

logger = logging.getLogger(__name__)
# Regexes stay in Python (not Fluent — catalogs are for UI copy only).
_RE_ANSI_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_RE_ANSI_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_RE_ANSI_ESC = re.compile(r"\x1b[@-Z\\-_]")
_RE_C0_NOISE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_RE_CR = re.compile(r"\r\n?")
_RE_REPEATED_FFFD = re.compile(r"\ufffd{2,}")


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
    """Update a Static with Markdown/Group/Text safely.

    Skips the update when the operator has an active text selection on *widget*
    so live refresh / re-render does not clear the selection before copy.
    """
    screen = getattr(widget, "screen", None)
    sels = getattr(screen, "selections", None) if screen is not None else None
    if sels and widget in sels:
        return
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


def _truncate_mid(
    s: str,
    head: int = 7000,
    tail: int = 5000,
    limit: int = 14000,
    *,
    truncate: bool = True,
) -> str:
    """Mid-body cap for *display*; when *truncate* is False keep the full string (yank)."""
    if not truncate or len(s) <= limit:
        return s
    return s[:head] + t("truncate-marker") + s[-tail:]


def _cap_str(s: str, limit: int, *, truncate: bool, marker: str | None = None) -> str:
    """Prefix cap for long fields; no-op when *truncate* is False.

    :param marker: Suffix after the cut. ``None`` uses the Fluent truncated
        marker; ``""`` means hard cut with no suffix (legacy search_replace).
    """
    if not truncate or len(s) <= limit:
        return s
    suffix = t("ui-truncated-1") if marker is None else marker
    return s[:limit] + suffix


def _render_tool_input(tname: str, ri: dict, *, truncate: bool = True) -> list:
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
            parts.append(Text(t("tool-input-file", path=str(fp)), style="cyan"))
        lang = _lang_from_path(str(fp)) or "text"
        old_s, new_s = (str(ri.get("old_string") or ""), str(ri.get("new_string") or ""))
        if old_s:
            parts.append(Text(t("tool-field-old-string"), style="red"))
            parts.append(
                _syntax(
                    _cap_str(old_s, 8000, truncate=truncate, marker=""), lang, line_numbers=True
                )
            )
        if new_s:
            parts.append(Text(t("tool-field-new-string"), style="green"))
            parts.append(
                _syntax(
                    _cap_str(new_s, 8000, truncate=truncate, marker=""), lang, line_numbers=True
                )
            )
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
            parts.append(Text(t("tool-input-target-file", path=str(tf)), style="cyan"))
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
            parts.append(Text(t("tool-field-pattern"), style="magenta"))
            parts.append(_syntax(str(pat), "text"))
        extra = {k: v for k, v in ri.items() if k != "pattern"}
        if extra:
            with suppress(Exception):
                parts.append(_syntax(json.dumps(extra, indent=2, ensure_ascii=False), "json"))
        return parts
    if tname == "list_dir":
        td = ri.get("target_directory") or path_hint
        if td:
            parts.append(Text(t("tool-input-target-directory", path=str(td)), style="blue"))
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
    # MCP via use_tool (or resolved name with tool_input payload).
    if tname in ("use_tool", "use-tool") or (
        isinstance(ri.get("tool_input"), dict) and ri.get("tool_name")
    ):
        mcp = str(ri.get("tool_name") or tname)
        parts.append(Text(t("tool-mcp-label", name=mcp), style="cyan"))
        ti = ri.get("tool_input")
        if isinstance(ti, dict):
            parts.append(Text(t("tool-input-section"), style="bright_blue"))
            try:
                parts.append(_syntax(json.dumps(ti, indent=2, ensure_ascii=False), "json"))
            except Exception:
                parts.append(Text(str(ti)))
        extra = {k: v for k, v in ri.items() if k not in ("tool_name", "tool_input")}
        if extra:
            with suppress(Exception):
                parts.append(_syntax(json.dumps(extra, indent=2, ensure_ascii=False), "json"))
        return parts
    if tname == "search_tool":
        q = ri.get("query")
        if q is not None:
            parts.append(Text(t("tool-field-query"), style="bright_blue"))
            parts.append(Text(str(q)))
        extra = {k: v for k, v in ri.items() if k != "query"}
        if extra:
            with suppress(Exception):
                parts.append(_syntax(json.dumps(extra, indent=2, ensure_ascii=False), "json"))
        return parts
    if tname in ("web_search", "spawn_subagent", "ask_user_question"):
        for key in ("query", "prompt", "description", "question"):
            if key in ri and isinstance(ri[key], str) and ri[key].strip():
                parts.append(Text(f"{key}:", style="bright_blue"))
                val = _cap_str(str(ri[key]), 4000, truncate=truncate)
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


def _render_tool_output(out: str, tname: str, path_hint: str, *, truncate: bool = True) -> list:
    """Syntax-highlighted tool output (trace_viewer output block)."""
    parts: list = []
    raw_len = len(out or "")
    cleaned = sanitize_console_text(out or "")
    if not cleaned and out:
        cleaned = sanitize_console_text(out, for_display=False) or t("tool-binary-output")
    n_out = len(cleaned)
    out_disp = _truncate_mid(cleaned, truncate=truncate)
    if raw_len and n_out < raw_len * 0.9:
        out_label = t("tool-output-rule-cleaned", n=n_out, raw=raw_len)
    else:
        out_label = t("tool-output-rule", n=n_out)
    parts.append(Rule(out_label, style="bright_black"))
    if not out_disp.strip():
        parts.append(Text(t("tool-empty-output"), style="dim italic"))
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
    # Plain Text for console dumps / large blobs — Pygments + Textual reflow on
    # every timeline keypress made the browser feel frozen (100ms–1s/event).
    if console_like or (lexer or "text") == "text" or len(out_disp) > 12_000:
        parts.append(Text(out_disp))
        return parts
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
    truncate: bool = True,
) -> Group:
    """Unified tool detail (trace_viewer render_tool_detail), call+result merged.

    :param truncate: When True (display), mid-cap huge tool bodies. When False
        (clipboard yank), keep full input/output text.
    """
    _ = (tool_call_id, update_index)
    ri = raw_input or {}
    path_hint = _path_hint(ri)
    tname = tool_name or "?"
    style = tool_style(tname)
    # Heading uses explicit separators (Fluent strips message edge whitespace).
    # Full-string Fluent variants exist (tool-detail-heading*) for non-Rich contexts.
    head = Text()
    head.append(f"#{index} ", style="dim")
    head.append("tool ", style="dim")
    head.append(tname, style=style if not is_error else "bold red")
    if is_error:
        head.append(" ✗ ERROR", style="bold red")
    # Avoid redundant "(tool_call)" / "(tool_result)" after the tool name.
    if event_type and event_type not in ("tool", *et.TOOL_TYPES, ""):
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
        parts.extend(_render_tool_input(tname, ri, truncate=truncate))
    else:
        parts += [
            Text(""),
            Rule(t("ui-input"), style="bright_black"),
            Text(t("tool-no-input"), style="dim italic"),
        ]
    parts.append(Text(""))
    parts.extend(_render_tool_output(output, tname, path_hint, truncate=truncate))
    return Group(*parts)


def render_tool_detail_from_event(
    ev: TraceEvent,
    *,
    paired_call: TraceEvent | None = None,
    paired_result: TraceEvent | None = None,
    duration: float | None = None,
    truncate: bool = True,
) -> Group:
    """Render tool_call / tool_result, merging pair when available (trace_viewer style)."""
    call = ev if ev.event_type == "tool_call" else paired_call
    result = ev if ev.event_type in et.TOOL_UPDATE_TYPES else paired_result
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
    is_err = bool(
        (result is not None and result.is_error)
        or ev.is_error
        or (call is not None and call.is_error)
    )
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
        truncate=truncate,
    )


def render_event_detail(
    ev: TraceEvent,
    *,
    finding: Finding | None = None,
    flag: Flag | None = None,
    duration: float | None = None,
    paired_call: TraceEvent | None = None,
    paired_result: TraceEvent | None = None,
    truncate: bool = True,
) -> RenderableType:
    """Full detail pane for any TraceEvent (trace_viewer render_event_detail + banners).

    :param truncate: Display caps for huge bodies (default). Pass False for
        clipboard yank so the operator gets the full event text.
    """
    banners: list = []
    if flag:
        ft = Text()
        ft.append(t("ui-flagged"), style="red bold")
        ft.append(f"[{flag.verdict.value}] {flag.description}\n", style="red")
        ft.append(t("flagged-at-when", when=flag.created_at), style="dim")
        banners.append(ft)
    if finding:
        sc = severity_style(finding.severity.value)
        it = Text()
        it.append(t("ui-finding"), style=f"{sc} bold")
        it.append(f"  [{finding.plugin_id}] {finding.category}: {finding.title}\n", style=sc)
        detail = finding.detail or ""
        if truncate and len(detail) > 400:
            detail = detail[:400]
        it.append(f"  {detail}", style="dim")
        banners.append(it)
    if ev.event_type in et.TOOL_TYPES:
        core = render_tool_detail_from_event(
            ev,
            paired_call=paired_call,
            paired_result=paired_result,
            duration=duration,
            truncate=truncate,
        )
        if banners:
            return Group(*banners, Text(""), core)
        return core
    from ..session.tagged_blocks import unwrap_for_display
    from ..session.turns import harness_user_chrome_heading

    chrome_heading = harness_user_chrome_heading(ev.content or "")
    style = KIND_STYLES.get(ev.event_type, "white")
    if chrome_heading is not None:
        style = "bold magenta"
    if ev.is_error and ev.event_type in et.SESSION_CHROME_TYPES:
        style = "bold red"
    head = Text()
    head.append(f"#{ev.index} ", style="dim")
    head.append(
        chrome_heading if chrome_heading is not None else (ev.type_label or ev.event_type),
        style=style,
    )
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
    body = _content_str(
        unwrap_for_display(ev.content or ""),
        sanitize=True,
        tool_name=ev.tool_name or "",
    )
    if truncate and len(body) > 20000:
        body = body[:10000] + t("truncate-marker") + body[-8000:]
    chunks: list = []
    if banners:
        chunks.extend(banners)
        chunks.append(Text(""))
    chunks.append(head)
    if ev.event_type in et.MESSAGE_TYPES and body.strip():
        # Soft newlines → Markdown hard breaks so each prompt line stays its own
        # visual line (selectable for partial copy). Blank lines stay paragraphs.
        md_body = "  \n".join(body.split("\n"))
        chunks += [Text(""), Markdown(md_body)]
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
    elif ev.event_type in et.SESSION_CHROME_TYPES:
        chunks += [
            Text(""),
            Text(body or "(empty)", style="bold red" if ev.is_error else "yellow"),
        ]
    elif body.strip():
        chunks += [Text(""), Text(body)]
    else:
        chunks += [Text(""), Text("(empty)", style="dim")]
    return Group(*chunks)


def render_markdown_doc(
    text: str, *, max_chars: int = 120000, truncate: bool = True
) -> RenderableType:
    """Markdown document for Summary / Feedback tabs.

    :param truncate: Cap huge docs for display. False keeps full text (yank).
    """
    body = text or "_empty_"
    if truncate and len(body) > max_chars:
        body = body[: max_chars // 2] + t("truncate-for-display") + body[-(max_chars // 3) :]
    try:
        return Markdown(body)
    except Exception:
        return Text(body)

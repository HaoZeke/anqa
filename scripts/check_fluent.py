#!/usr/bin/env python3
"""Lint Fluent catalog + call-site construction (AGENTS.md §3.3 / §3.3a).

Hard failures (exit 1):
- f-string embedding t(...) under groket/
- re.compile(t(...))
- regex/binary message ids in FTL
- leading/trailing whitespace on single-line FTL values without placeables
- Rich style tags in FTL passed into ``Text(...)`` / ``Text.append(..., style=)``
  (those APIs treat the string as literal, so ``[bold yellow]`` shows on screen)
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FTL = ROOT / "groket" / "locale" / "en" / "main.ftl"
PY_ROOT = ROOT / "groket"

_MSG = re.compile(r"^([a-zA-Z0-9_-]+)\s*=\s*(.*)$")
_REGEX_IDS = frozenset({"ui-x1b", "ui-x1b-x07-x1b", "ui-r-n", "ui-ufffd-2"})
_RE_COMPILE_T = re.compile(r"re\.compile\s*\(\s*t\s*\(")
# f"…{t( or f'…{t(
_F_STRING_T = re.compile(r"""f["'][^"']*\{[^{}]*\bt\s*\(""")
# multi-line FTL continuation lines start with spaces and are part of prior message
_MULTILINE_CONT = re.compile(r"^\s+\S")
_RICH_TAG = re.compile(
    r"\[/?(?:bold|dim|italic|underline|strike|reverse|blink|"
    r"yellow|red|green|cyan|blue|magenta|white|black)(?:\s|[/\]])|\[/\]",
    re.IGNORECASE,
)


def check_ftl() -> list[str]:
    hard: list[str] = []
    if not FTL.is_file():
        return [f"missing {FTL}"]
    lines = FTL.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _MSG.match(line)
        if not m:
            continue
        mid, val = m.group(1), m.group(2)
        if mid in _REGEX_IDS:
            hard.append(
                f"{FTL.relative_to(ROOT)}:{i}: regex/constant id {mid!r} must not live in FTL"
            )
        # Multi-line FTL: value empty on first line, body indented below
        if val.strip() == "":
            continue
        if "{$" in val or val.strip().startswith("{"):
            continue
        # Single-line value with edge spaces (Fluent strips — not a glue strategy)
        if val.startswith(" ") or (val.endswith(" ") and not val.endswith("\\ ")):
            hard.append(
                f"{FTL.relative_to(ROOT)}:{i}: {mid!r} has leading/trailing space "
                f"(Fluent strips edges; use full messages or join_ui)"
            )
    return hard


def check_py() -> list[str]:
    errs: list[str] = []
    for path in PY_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT)
        for i, line in enumerate(text.splitlines(), 1):
            if _RE_COMPILE_T.search(line):
                errs.append(f"{rel}:{i}: re.compile(t(...)) — keep regexes in Python, not Fluent")
            if _F_STRING_T.search(line):
                errs.append(
                    f"{rel}:{i}: f-string embeds t() — use t('id', var=...) or join_ui(...)"
                )
    return errs


def parse_ftl_messages(text: str) -> dict[str, str]:
    """Map Fluent message id → raw value (including indented continuations)."""
    out: dict[str, str] = {}
    cur: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        m = _MSG.match(line)
        if m:
            if cur is not None:
                out[cur] = "\n".join(buf)
            cur, rest = m.group(1), m.group(2)
            buf = [rest]
            continue
        if cur is not None and _MULTILINE_CONT.match(line):
            buf.append(line)
            continue
        if cur is not None:
            out[cur] = "\n".join(buf)
            cur = None
            buf = []
    if cur is not None:
        out[cur] = "\n".join(buf)
    return out


def marked_fluent_ids(ftl_text: str) -> set[str]:
    """Message ids whose values contain Rich style tags."""
    return {mid for mid, val in parse_ftl_messages(ftl_text).items() if _RICH_TAG.search(val)}


def _t_message_id(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    if name != "t" or not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _feeds_literal_text(node: ast.Call) -> bool:
    """True when *node* is ``Text(...)`` or ``.append(..., style=...)``."""
    func = node.func
    if isinstance(func, ast.Name) and func.id == "Text":
        return True
    if isinstance(func, ast.Attribute) and func.attr == "append":
        return any(kw.arg == "style" for kw in node.keywords)
    return False


def check_markup_into_text(py_src: str, marked: set[str], rel: str) -> list[str]:
    """Flag Rich-tagged Fluent ids fed into ``Text`` / styled ``append``."""
    if not marked:
        return []
    try:
        tree = ast.parse(py_src)
    except SyntaxError:
        return [f"{rel}:1: could not parse Python for Fluent markup check"]
    errs: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if not _feeds_literal_text(node):
            continue
        mid = _t_message_id(node.args[0])
        if mid is None or mid not in marked:
            continue
        errs.append(
            f"{rel}:{node.lineno}: {mid!r} has Rich tags in FTL but is passed to "
            f"Text/append(style=) — those treat the string as literal. "
            f"Keep the Fluent value plain and apply style= in Python."
        )
    return errs


def check_markup_call_sites() -> list[str]:
    if not FTL.is_file():
        return []
    marked = marked_fluent_ids(FTL.read_text(encoding="utf-8"))
    errs: list[str] = []
    for path in PY_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        rel = str(path.relative_to(ROOT))
        errs.extend(check_markup_into_text(path.read_text(encoding="utf-8"), marked, rel))
    return errs


def main() -> int:
    hard = check_ftl() + check_py() + check_markup_call_sites()
    for e in hard:
        print(f"ERROR: {e}", file=sys.stderr)
    if hard:
        print(f"check_fluent: {len(hard)} error(s)", file=sys.stderr)
        return 1
    print("check_fluent: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""fzf-style fuzzy matching with scoring and highlighting."""

from __future__ import annotations

import re
from collections.abc import Sequence

from rich.style import Style
from rich.text import Text

MATCH_STYLE = Style(bold=True, color="cyan")
_SCORE_MATCH = 16
_BONUS_BOUNDARY = 8
_BONUS_BOUNDARY_WHITE = 10
_BONUS_CAMEL = 10
_BONUS_CONSECUTIVE = 4
_BONUS_FIRST_MULT = 2
_PENALTY_GAP_START = -3
_PENALTY_GAP_EXTEND = -1
_BOUNDARY_CHARS = frozenset("/-_. ,;:\\")


def _char_bonus(prev: str | None, curr: str) -> int:
    """Compute fzf-style position bonus for a character."""
    if prev is None:
        return _BONUS_BOUNDARY_WHITE
    if prev in _BOUNDARY_CHARS:
        return _BONUS_BOUNDARY_WHITE if prev in (" ", "\t") else _BONUS_BOUNDARY
    if prev.islower() and curr.isupper():
        return _BONUS_CAMEL
    return 0


def _fzf_score(query: str, candidate: str) -> tuple[float, list[int]]:
    """fzf-style fuzzy match: subsequence with boundary/consecutive bonuses.

    Returns (score, matched_positions).  Score <= 0 means no match.
    """
    ql = query.lower()
    cl = candidate.lower()
    n, m = (len(cl), len(ql))
    if m == 0:
        return (0, [])
    if m > n:
        return (0, [])
    positions: list[int] = []
    j = 0
    for i in range(n):
        if j < m and cl[i] == ql[j]:
            positions.append(i)
            j += 1
    if j < m:
        return (0, [])
    score = 0.0
    consecutive = 0
    for k, pos in enumerate(positions):
        prev = candidate[pos - 1] if pos > 0 else None
        bonus = _char_bonus(prev, candidate[pos])
        char_score = _SCORE_MATCH + bonus
        if k == 0 and bonus > 0:
            char_score += bonus * (_BONUS_FIRST_MULT - 1)
        if consecutive > 0:
            char_score += _BONUS_CONSECUTIVE
        else:
            gap = pos - (positions[k - 1] + 1) if k > 0 else 0
            if gap > 0:
                char_score += _PENALTY_GAP_START + _PENALTY_GAP_EXTEND * (gap - 1)
        if query[k] == candidate[pos]:
            char_score += 1
        score += max(0, char_score)
        if k > 0 and pos == positions[k - 1] + 1:
            consecutive += 1
        else:
            consecutive = 0
    return (score, positions)


def _highlight(text: str, positions: list[int]) -> Text:
    """Build a Rich Text with matched positions highlighted."""
    result = Text(text)
    for pos in positions:
        result.stylize(MATCH_STYLE, pos, pos + 1)
    return result


def split_tokens(query: str) -> list[str]:
    """Split a query into tokens on boundary characters."""
    return [t for t in re.split("[_\\-./\\s]+", query) if len(t) >= 2]


def mark_unified_hit(unified: str, hit_line: int | None) -> str:
    """Prefix ``> `` on the hit unified line so the open hunk shows the land."""
    lines = (unified or "").splitlines()
    if not lines:
        return ""
    out: list[str] = []
    for i, line in enumerate(lines):
        mark = "> " if hit_line is not None and i == hit_line else "  "
        out.append(f"{mark}{line}")
    return "\n".join(out)


def first_match_line(query: str, text: str) -> int | None:
    """0-based line of the first fuzzy hit in *text*, or ``None``."""
    lines = match_lines(query, text)
    return lines[0] if lines else None


def match_lines(query: str, text: str) -> list[int]:
    """0-based lines in *text* that fuzzy-match *query*."""
    q = (query or "").strip()
    if not q:
        return []
    hits = [i for i, line in enumerate((text or "").splitlines()) if _fzf_score(q, line)[0] > 0]
    if hits:
        return hits
    score, _ = _fzf_score(q, text or "")
    return [0] if score > 0 else []


def iter_diff_hits(query: str, hunks: Sequence[tuple[str, str]]) -> list[tuple[str, int | None]]:
    """Every find hit in file order: each matching body line, or a path-only file.

    :returns: ``(path, line)`` with ``line is None`` for a path-only hit.
    """
    q = (query or "").strip()
    if not q:
        return []
    hits: list[tuple[str, int | None]] = []
    for path, unified in hunks:
        lines = match_lines(q, unified)
        if lines:
            hits.extend((path, i) for i in lines)
        elif _fzf_score(q, path)[0] > 0:
            hits.append((path, None))
    return hits


def filter_diff_hunks(
    query: str, hunks: Sequence[tuple[str, str]]
) -> list[tuple[str, float, int | None, str]]:
    """Rank Diff files by path, then unified body, with the catalog fzf scorer.

    *hunks* is ``(path, unified)``. Empty *query* keeps every file in order.
    A path hit has ``line is None`` and ``where == "path"``. A body-only hit
    sets ``line`` to the first matching unified line and ``where == "body"``.

    :returns: ``(path, score, line, where)`` highest score first.
    """
    q = (query or "").strip()
    if not q:
        return [(path, 0.0, None, "path") for path, _unified in hunks]
    hits: list[tuple[str, float, int | None, str]] = []
    for path, unified in hunks:
        path_score, _ = _fzf_score(q, path)
        if path_score > 0:
            hits.append((path, float(path_score), None, "path"))
            continue
        body_score, _ = _fzf_score(q, unified)
        if body_score > 0:
            hits.append((path, float(body_score), first_match_line(q, unified), "body"))
    hits.sort(key=lambda row: -row[1])
    return hits


def fzf_match(query: str, candidate: str) -> tuple[float, Text]:
    """Full fzf-style fuzzy match with highlighting.

    Returns (score, highlighted_text).  Score <= 0 means no match.
    Falls back to token-substring matching when subsequence fails.
    """
    score, positions = _fzf_score(query, candidate)
    if score > 0:
        return (score, _highlight(candidate, positions))
    ql = query.lower()
    cl = candidate.lower()
    best_score = 0.0
    best_positions: list[int] = []
    for token in split_tokens(ql):
        idx = cl.find(token)
        if idx >= 0:
            t_positions = list(range(idx, idx + len(token)))
            bonus = _char_bonus(candidate[idx - 1] if idx > 0 else None, candidate[idx])
            t_score = len(token) * _SCORE_MATCH + bonus * 2
            if t_score > best_score:
                best_score = t_score
                best_positions = t_positions
    if best_score > 0:
        return (best_score, _highlight(candidate, best_positions))
    return (0, Text(candidate))

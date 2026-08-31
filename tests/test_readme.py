"""Product README is a start page that tracks shipped help and control docs."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
HUD_README = ROOT / "desktop" / "README.md"
HELP = ROOT / "anqa" / "locale" / "en" / "help.rich.txt"
CONTROL = ROOT / "docs" / "control.md"
AGENTS = ROOT / "AGENTS.md"

_TOOLKIT = re.compile(r"Textual|icedtea|\biced\b")
_VOICE = re.compile(r"first path|operator first path|operator surface|operator protocol")
_METHODS = re.compile(r"session/list|session/import|session/render")


def _help_rows() -> list[tuple[str, str]]:
    section = ""
    rows: list[tuple[str, str]] = []
    for line in HELP.read_text(encoding="utf-8").splitlines():
        if line.startswith("[bold]") and "[/bold]" in line:
            section = line.removeprefix("[bold]").split("[/bold]", 1)[0].strip()
            continue
        if not line.startswith("  ") or section == "":
            continue
        matched = re.match(r"^  (.+?)\s{2,}([A-Za-z].*)$", line)
        if matched is None:
            continue
        rows.append((matched.group(1).strip(), matched.group(2).strip()))
    return rows


def test_readme_opens_with_what_anqa_does() -> None:
    text = README.read_text(encoding="utf-8")
    head = "\n".join(text.splitlines()[:20])
    assert "review" in head
    assert "harness" in head.lower()
    assert "it is not the Grok" not in head.lower()


def test_readme_has_four_client_headings() -> None:
    text = README.read_text(encoding="utf-8")
    for heading in (
        "## Terminal app",
        "## Desktop HUD",
        "## Emacs",
        "## Neovim (0.9+)",
        "## Control",
    ):
        assert heading in text, heading


def test_help_rich_omits_eval_era_follow_up_label() -> None:
    help_text = HELP.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    assert "Follow-up" not in help_text
    assert "follow-up" not in help_text
    assert "Follow-up" not in readme
    assert "| n / e |" not in readme


def test_help_rich_actions_appear_in_readme_key_table() -> None:
    readme = README.read_text(encoding="utf-8")
    rows = _help_rows()
    assert rows, "help.rich.txt has no key rows"
    missing = [f"{key} {action}" for key, action in rows if action not in readme]
    assert missing == [], missing
    assert "| s / Space |" in readme


def test_readmes_have_no_toolkit_or_process_labels() -> None:
    for path in (README, HUD_README):
        text = path.read_text(encoding="utf-8")
        assert _TOOLKIT.search(text) is None, path.name
        assert _VOICE.search(text) is None, path.name
    assert _VOICE.search(CONTROL.read_text(encoding="utf-8")) is None


def test_method_inventory_lives_only_in_control_doc() -> None:
    control = CONTROL.read_text(encoding="utf-8")
    for method in ("session/list", "session/import", "session/render"):
        assert method in control, method
    assert "session/selected" in control
    assert "notes/changed" in control
    assert "session/changed" in control
    assert "Content-Length" in control
    assert "ANQA_CONTROL_SOCKET" in control
    for path in (README, AGENTS):
        assert _METHODS.search(path.read_text(encoding="utf-8")) is None, path.name
    assert "docs/control.md" in AGENTS.read_text(encoding="utf-8")


def test_hud_readme_states_three_second_poll_and_links_clients() -> None:
    text = HUD_README.read_text(encoding="utf-8")
    assert "3 seconds" in text
    assert "../README.md#desktop-hud" in text
    assert "../docs/control.md" in text
    assert "../README.md#emacs" in text
    assert "../README.md#neovim-09" in text
    assert "fail-under" not in text
    assert "app_id" not in text


def test_readme_has_no_html_heading_anchors() -> None:
    assert "<a id" not in README.read_text(encoding="utf-8")
    assert "<a id" not in HUD_README.read_text(encoding="utf-8")


def test_readme_mark_switches_with_github_color_scheme() -> None:
    text = README.read_text(encoding="utf-8")
    light = ROOT / "brand" / "png" / "anqa-lockup-stacked.png"
    dark = ROOT / "brand" / "png" / "anqa-lockup-stacked-on-dark.png"
    assert light.is_file()
    assert dark.is_file()
    assert "anqa-lockup-stacked.png#gh-light-mode-only" in text
    assert "anqa-lockup-stacked-on-dark.png#gh-dark-mode-only" in text
    assert 'alt="anqa"' in text
    assert text.lstrip().startswith('<p align="center">')
    assert not any(line.startswith("# anqa") for line in text.splitlines())
    assert "anqa-mark-reverse.png" not in text
    assert "anqa-lockup-horizontal.png" not in text

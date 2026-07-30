"""Built-in export renderers (markdown / org / plain)."""

from __future__ import annotations

from groket.session.export_render import (
    BUILTIN_RENDERERS,
    analysis_report_from_result,
    report_file_extension,
)


def test_builtin_ids_include_org() -> None:
    assert "org" in BUILTIN_RENDERERS
    assert "markdown" in BUILTIN_RENDERERS
    assert "plain" in BUILTIN_RENDERERS
    assert report_file_extension("org") == ".org"
    assert report_file_extension("markdown") == ".md"
    assert report_file_extension("plain") == ".txt"


def test_org_synthesizes_findings() -> None:
    body = analysis_report_from_result(
        {
            "analyzer_id": "engine",
            "ok": True,
            "summary": "2 findings",
            "findings": [
                {
                    "title": "Bad edit",
                    "severity": "high",
                    "detail": "Wrong path",
                    "category": "Correctness",
                    "event_indices": [3],
                }
            ],
            "artifacts": {},
        },
        plugin_stem="engine",
        renderer="org",
    )
    assert "#+TITLE: Analysis report — engine" in body
    assert "* Summary" in body
    assert "* Findings" in body
    assert "** 1. Bad edit (HIGH)" in body
    assert "Wrong path" in body
    assert "#3" in body


def test_org_adapts_markdown_report_artifact() -> None:
    body = analysis_report_from_result(
        {
            "analyzer_id": "x",
            "artifacts": {"report": "# From plugin\n\n## Section\n\nbody\n"},
        },
        plugin_stem="x",
        renderer="org",
    )
    assert "#+TITLE: From plugin" in body
    assert "* Section" in body
    assert "body" in body

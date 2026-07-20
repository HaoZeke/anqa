# Operator notes schema example

Turn-linked operator notes use a **configurable** field list. Copy this file:

```bash
mkdir -p ~/.groket
cp examples/notes/notes_schema.example.toml ~/.groket/notes_schema.toml
```

Then edit `id` / `label` for your workflow. Field ids must be
`^[A-Za-z_][A-Za-z0-9_-]*$`. Keep program-specific templates in your local kit,
not in the groket package.

## Session file

Notes are stored as `<session_dir>/operator_notes.toml` (fallback:
`~/.groket/notes/<session_id>/operator_notes.toml`).

## TUI

In the session browser, press **`N`** to **add** a note (create-only; linked to
the current turn and optional selected event). Report tab lists notes. Export
(`E`) includes `notes/operator_notes.toml` when notes exist.

**Authoring is TUI-only** — batch does not write notes.

## Ingest

External tools can parse the TOML from the export tarball without scraping the
Report markdown.

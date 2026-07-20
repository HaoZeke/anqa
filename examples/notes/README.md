# Operator notes schema example

Turn-linked operator notes use a **configurable** field list. Copy this file:

```bash
mkdir -p ~/.groket
cp examples/notes/notes_schema.example.toml ~/.groket/notes_schema.toml
```

Then edit `id` / `label` / `multiline` / `required` / `choices` for your workflow.
Do not put program-specific templates in the groket package — keep those in your local kit.

## Session file

Notes are stored as `<session_dir>/operator_notes.toml` (fallback:
`~/.groket/notes/<session_id>/operator_notes.toml`).

## TUI

In the session browser, press **`N`** to add a note (linked to the current turn
and optional selected event). Report tab shows the Notes section. Export (`E`)
includes `notes/operator_notes.toml` and a schema snapshot when notes exist.

## Ingest

External tools can parse the TOML from the export tarball without scraping the
Report markdown. Schema snapshot path: `notes/schema.toml`.

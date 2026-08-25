# Examples

**Supported reference packs** — CI and `just examples-check` refuse to break
them. Copy into `~/.groket/` or pass paths explicitly. Nothing under
`examples/` is auto-loaded by the product.

| Pack | What it teaches | Install / use |
|------|-----------------|---------------|
| [`config/`](config/) | Prefs TOML (`config.toml`) | `~/.groket/config.toml` |
| [`notes/`](notes/) | In-app notes form schema (`source` is required on every write; extra fields are kept) | `~/.groket/notes_schema.toml` |
| [`keys/`](keys/) | Key overlay (`colemak.toml`) | `~/.groket/keys.toml` |
| [`themes/`](themes/) | Named colorway (`paper.toml`) | `~/.groket/themes/` |

## Contract

```bash
just examples-check   # or: uv run python scripts/check_examples.py
```

Validates keys overlays (`groket keys --check`), prefs, notes schema,
and pack READMEs. Part of `just ci`.

# Localization (Project Fluent)

Anqa uses **[Fluent](https://projectfluent.org/)** (`.ftl` files), not gettext/PO.

| Path | Role |
|------|------|
| `en/main.ftl` | Source language (English) message catalog |
| `fr/main.ftl` | Example: copy `en/main.ftl` and translate values |
| `anqa/ui/i18n.py` | `setup_i18n()`, `t("message-id", **vars)` |
| `anqa/ui/text.py` | Named helpers → Fluent IDs (`save` → `t("save")`) |

## Language selection

Default language is English (`en`). Call ``setup_i18n("fr")`` (or another
catalog under ``locale/<lang>/``) when you add translations.

## Message format

```ftl
save = Save
model-filter-notify = Model filter: { $label }
help-markup =
    | [bold]anqa — keyboard[/bold]
    | …
```

In Python:

```python
from anqa.ui.i18n import t
from anqa.ui import text as U

U.save()  # t("save")
t("model-filter-notify", label="gpt")
```

## Adding a language

```bash
mkdir -p anqa/locale/de
cp anqa/locale/en/main.ftl anqa/locale/de/main.ftl
# Translate the right-hand sides; keep message ids and { $var } names.
```

Fallback order: requested locale → `en`.

## Long Rich / special markup

Fluent treats `[…]` as syntax, so the `?` help panel is **not** in `main.ftl`.
It lives in `en/help.rich.txt` (copy per locale). Short labels stay in Fluent.


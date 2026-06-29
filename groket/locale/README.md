# Localization (Project Fluent)

Groket uses **[Fluent](https://projectfluent.org/)** (`.ftl` files), not gettext/PO.

| Path | Role |
|------|------|
| `en/main.ftl` | Source language (English) message catalog |
| `fr/main.ftl` | Example: copy `en/main.ftl` and translate values |
| `../i18n.py` | `setup_i18n()`, `t("message-id", **vars)` |
| `../ui_text.py` | Named helpers → Fluent IDs (`save` → `t("save")`) |

## Language selection

```bash
GROKET_LANG=fr groket
# or LANG=fr_FR.UTF-8
```

## Message format

```ftl
save = Save
model-filter-notify = Model filter: { $label }
help-markup =
    | [bold]groket — keyboard[/bold]
    | …
```

In Python:

```python
from groket.i18n import t
from groket import ui_text as U

U.save()  # t("save")
t("model-filter-notify", label="gpt")
```

## Adding a language

```bash
mkdir -p groket/locale/de
cp groket/locale/en/main.ftl groket/locale/de/main.ftl
# Translate the right-hand sides; keep message ids and { $var } names.
```

Fallback order: requested locale → `en`.

## Long Rich / special markup

Fluent treats `[…]` as syntax, so the `?` help panel is **not** in `main.ftl`.
It lives in `en/help.rich.txt` (copy per locale). Short labels stay in Fluent.


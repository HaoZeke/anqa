"""Textual themes that use the groket brand palette.

Ink / cream for chrome. Caps for success / error / warning. No cyan.
"""

from __future__ import annotations

from textual.theme import Theme

from .appearance import Appearance

# Same hex as brand/build.py.
INK = "#282828"
CREAM = "#FBF1C7"
COMPLETE = "#98971A"
FAILED = "#CC241D"
RUNNING = "#D79921"
CANCELLED = "#928374"
# Lifted ink for panels (gruvbox bg1 / bg2).
INK_LIFT = "#3c3836"
INK_BOOST = "#504945"
CREAM_DIM = "#ebdbb2"
CREAM_LIFT = "#f2e5bc"

GROKET = Theme(
    name="groket",
    primary=COMPLETE,
    secondary=CANCELLED,
    accent=RUNNING,
    warning=RUNNING,
    error=FAILED,
    success=COMPLETE,
    foreground=CREAM,
    background=INK,
    surface=INK_LIFT,
    panel=INK_LIFT,
    boost=INK_BOOST,
    dark=True,
    variables={
        "block-cursor-foreground": INK,
        "button-color-foreground": INK,
        "footer-background": INK_LIFT,
        "footer-key-foreground": CREAM,
        "footer-description-foreground": CANCELLED,
    },
)

# Textual ships dark ``gruvbox`` only. Light member matches that face.
GRUVBOX_LIGHT = Theme(
    name="gruvbox-light",
    primary="#689d6a",
    secondary="#7c6f64",
    warning="#d65d0e",
    error="#9d0006",
    success="#79740e",
    accent="#b57614",
    foreground="#3c3836",
    background="#fbf1c7",
    surface="#ebdbb2",
    panel="#d5c4a1",
    dark=False,
    variables={
        "block-cursor-foreground": "#282828",
        "input-selection-background": "#689d6a40",
        "button-color-foreground": "#fbf1c7",
    },
)

GROKET_LIGHT = Theme(
    name="groket-light",
    primary=COMPLETE,
    secondary=CANCELLED,
    accent=RUNNING,
    warning=RUNNING,
    error=FAILED,
    success=COMPLETE,
    foreground=INK,
    background=CREAM,
    surface=CREAM_LIFT,
    panel=CREAM_DIM,
    boost="#d5c4a1",
    dark=False,
    variables={
        "block-cursor-foreground": CREAM,
        "button-color-foreground": CREAM,
        "footer-background": CREAM_DIM,
        "footer-key-foreground": INK,
        "footer-description-foreground": CANCELLED,
    },
)


def register_brand_themes(app: object) -> None:
    """Register groket themes on a Textual app.

    :param app: ``App`` with ``register_theme``.
    """
    register = getattr(app, "register_theme", None)
    if not callable(register):
        return
    register(GROKET)
    register(GROKET_LIGHT)
    register(GRUVBOX_LIGHT)


# id / light / dark → (light, dark)
_PAIRS: dict[str, tuple[str, str]] = {}
for _id, _pair in {
    "groket": ("groket-light", "groket"),
    "gruvbox": ("gruvbox-light", "gruvbox"),
    "textual": ("textual-light", "textual-dark"),
    "solarized": ("solarized-light", "solarized-dark"),
    "atom-one": ("atom-one-light", "atom-one-dark"),
    "ansi": ("ansi-light", "ansi-dark"),
    "catppuccin": ("catppuccin-latte", "catppuccin-mocha"),
    "rose-pine": ("rose-pine-dawn", "rose-pine"),
}.items():
    _PAIRS[_id] = _pair
    _PAIRS[_pair[0]] = _pair
    _PAIRS[_pair[1]] = _pair


def resolve_theme(pref: str, desktop: Appearance) -> str:
    """Family member for ``pref`` when ``follow_os`` is on. Unpaired names stay."""
    key = pref.strip()
    pair = _PAIRS.get(key)
    if pair is None:
        return key
    return pair[0] if desktop == "light" else pair[1]

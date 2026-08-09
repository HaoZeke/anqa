//! Flat chrome painted with TUI theme tokens (8px rhythm, almost no rounding).

use iced::border::Radius;
use iced::widget::{button, container, pick_list, text_input};
use iced::{Background, Border, Color, Shadow};

use crate::theme::{mix, Tokens};

const R0: Radius = Radius {
    top_left: 0.0,
    top_right: 0.0,
    bottom_right: 0.0,
    bottom_left: 0.0,
};
const R4: Radius = Radius {
    top_left: 4.0,
    top_right: 4.0,
    bottom_right: 4.0,
    bottom_left: 4.0,
};

fn tint(tok: Tokens, c: Color, amount: f32) -> Color {
    mix(c, tok.canvas, amount)
}

pub fn fill(bg: Color, fg: Color) -> container::Style {
    container::Style {
        background: Some(Background::Color(bg)),
        text_color: Some(fg),
        ..container::Style::default()
    }
}

pub fn hairline(tok: Tokens) -> container::Style {
    container::Style {
        background: Some(Background::Color(tok.border)),
        ..container::Style::default()
    }
}

pub fn card(tok: Tokens, focus: bool) -> container::Style {
    container::Style {
        background: Some(Background::Color(tok.card)),
        text_color: Some(tok.text),
        border: Border {
            color: if focus { tok.primary } else { tok.border },
            width: 1.0,
            radius: R0,
        },
        shadow: Shadow::default(),
    }
}

pub fn shell(tok: Tokens) -> container::Style {
    container::Style {
        background: Some(Background::Color(tok.canvas)),
        text_color: Some(tok.text),
        border: Border {
            color: tok.primary,
            width: 1.0,
            radius: R0,
        },
        shadow: Shadow::default(),
    }
}

pub fn footer(tok: Tokens) -> container::Style {
    container::Style {
        background: Some(Background::Color(tok.panel)),
        text_color: Some(tok.muted),
        ..container::Style::default()
    }
}

pub fn inset(tok: Tokens) -> container::Style {
    container::Style {
        background: Some(Background::Color(mix(tok.text, tok.canvas, 0.08))),
        text_color: Some(tok.text),
        border: Border {
            color: tok.border,
            width: 1.0,
            radius: R4,
        },
        shadow: Shadow::default(),
    }
}

pub fn list_row(tok: Tokens, selected: bool) -> container::Style {
    container::Style {
        background: Some(Background::Color(if selected {
            tok.selected
        } else {
            tok.panel
        })),
        text_color: Some(if selected {
            tok.selected_text
        } else {
            tok.text
        }),
        border: Border {
            color: Color::TRANSPARENT,
            width: 0.0,
            radius: R0,
        },
        ..container::Style::default()
    }
}

pub fn tab(tok: Tokens, active: bool) -> impl Fn(&iced::Theme, button::Status) -> button::Style {
    move |_theme, status| {
        let bg = if active {
            Some(Background::Color(tint(tok, tok.primary, 0.28)))
        } else if matches!(status, button::Status::Hovered | button::Status::Pressed) {
            Some(Background::Color(tint(tok, tok.text, 0.08)))
        } else {
            None
        };
        button::Style {
            background: bg,
            text_color: if active { tok.text } else { tok.muted },
            border: Border {
                color: Color::TRANSPARENT,
                width: 0.0,
                radius: R0,
            },
            shadow: Shadow::default(),
        }
    }
}

pub fn quiet(tok: Tokens) -> impl Fn(&iced::Theme, button::Status) -> button::Style {
    move |_theme, status| {
        let bg = match status {
            button::Status::Hovered | button::Status::Pressed => {
                Some(Background::Color(tint(tok, tok.text, 0.14)))
            }
            _ => Some(Background::Color(tint(tok, tok.text, 0.08))),
        };
        button::Style {
            background: bg,
            text_color: tok.text,
            border: Border {
                color: Color::TRANSPARENT,
                width: 0.0,
                radius: R4,
            },
            shadow: Shadow::default(),
        }
    }
}

pub fn chip(tok: Tokens) -> impl Fn(&iced::Theme, button::Status) -> button::Style {
    move |_theme, status| {
        let a = if matches!(status, button::Status::Hovered | button::Status::Pressed) {
            0.18
        } else {
            0.10
        };
        button::Style {
            background: Some(Background::Color(tint(tok, tok.text, a))),
            text_color: tok.muted,
            border: Border {
                color: Color::TRANSPARENT,
                width: 0.0,
                radius: R4,
            },
            shadow: Shadow::default(),
        }
    }
}

pub fn danger(tok: Tokens) -> impl Fn(&iced::Theme, button::Status) -> button::Style {
    move |_theme, status| {
        let a = if matches!(status, button::Status::Hovered | button::Status::Pressed) {
            0.36
        } else {
            0.22
        };
        button::Style {
            background: Some(Background::Color(tint(tok, tok.error, a))),
            text_color: tok.error,
            border: Border {
                color: Color::TRANSPARENT,
                width: 0.0,
                radius: R4,
            },
            shadow: Shadow::default(),
        }
    }
}

pub fn search(tok: Tokens) -> impl Fn(&iced::Theme, text_input::Status) -> text_input::Style {
    move |_theme, status| {
        let border = match status {
            text_input::Status::Focused => Border {
                color: tok.primary,
                width: 1.0,
                radius: R4,
            },
            _ => Border {
                color: tok.border,
                width: 1.0,
                radius: R4,
            },
        };
        text_input::Style {
            background: Background::Color(tok.panel),
            border,
            icon: tok.muted,
            placeholder: tok.muted,
            value: tok.text,
            selection: tok.selected,
        }
    }
}

pub fn picker(tok: Tokens) -> impl Fn(&iced::Theme, pick_list::Status) -> pick_list::Style {
    move |_theme, status| {
        let border = match status {
            pick_list::Status::Opened | pick_list::Status::Hovered => Border {
                color: tok.primary,
                width: 1.0,
                radius: R4,
            },
            pick_list::Status::Active => Border {
                color: tok.border,
                width: 1.0,
                radius: R4,
            },
        };
        pick_list::Style {
            text_color: tok.text,
            placeholder_color: tok.muted,
            handle_color: tok.muted,
            background: Background::Color(tok.panel),
            border,
        }
    }
}

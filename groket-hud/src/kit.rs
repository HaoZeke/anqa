//! HUD chrome built on icedtea constructors.
//!
//! Prefer icedtea public APIs directly. Helpers here exist only when icedtea
//! is missing a parameter (per-tab disable, custom search placeholder/submit).

use iced::widget::{column, container, row, text, Space};
use iced::{Alignment, Element, Length};
use icedtea::a11y::{A11y, Role};
use icedtea::collection::Tabs;
use icedtea::i18n::Direction;
use icedtea::theme::Tokens;
use icedtea::toast::ToastKind;
use icedtea::typo::FontFace;
use icedtea::widget;

use crate::app::Message;
use crate::model::Tab;
use crate::typo;

/// icedtea [`layout::FORM_LABEL`] gutter for Overview (and any form stacks).
pub const LABEL_GUTTER: f32 = icedtea::layout::FORM_LABEL;

/// Determinate context / fill bar — icedtea [`widget::progress`].
pub fn context_progress<'a>(frac: f32, tea: Tokens) -> Element<'a, Message> {
    let label = widget::progress_label(frac, None);
    widget::progress(
        frac.clamp(0.0, 1.0),
        None,
        Some(label.as_str()),
        false,
        tea,
        A11y::new("context", Role::Progress).with_value(label.clone()),
    )
}

/// Empty / loading shell — icedtea [`pattern::status_page`].
pub fn status_empty<'a>(
    title: impl Into<String>,
    detail: impl Into<String>,
    tea: Tokens,
) -> Element<'a, Message> {
    icedtea::pattern::status_page(title, detail, None, tea)
}

/// Search field: icedtea [`widget::themed_text_input`] with a leading search icon.
///
/// [`widget::search_input`] still hard-codes the placeholder and has no submit
/// or input id. This keeps those parameters and uses [`widget::FieldOpts`] for
/// the Material prefix.
pub fn search_field<'a>(
    placeholder: &str,
    value: &str,
    on_input: impl Fn(String) -> Message + 'a,
    on_submit: Option<Message>,
    tea: Tokens,
    a11y: A11y,
    input_id: Option<iced::widget::Id>,
) -> Element<'a, Message> {
    widget::themed_text_input(
        placeholder,
        value,
        on_input,
        on_submit,
        widget::FieldOpts {
            face: widget::FieldFace::Filled,
            icons: icedtea::icon::Icons::leading(icedtea::icon::Icon::Search),
            label: "",
            max_len: None,
        },
        tea,
        a11y,
        input_id,
    )
}

/// Browse pane tabs via icedtea [`widget::tab_bar`] when all tabs are enabled.
///
/// When some tabs must stay disabled (no session yet), paint the same underbar
/// strip with per-tab enable flags — contribution candidate for icedtea
/// `tab_bar` (`enabled: &[bool]` or similar).
pub fn pane_tabs<'a>(
    active: Tab,
    session_ready: bool,
    tabs: &'static [Tab],
    tea: Tokens,
) -> Element<'a, Message> {
    let titles: Vec<String> = tabs.iter().map(|t| t.label().to_string()).collect();
    let active_i = tabs.iter().position(|t| *t == active).unwrap_or(0);

    if session_ready {
        let mut bar = Tabs::new(titles);
        bar.select(active_i);
        bar.closable = false;
        return widget::tab_bar(
            &bar,
            |i| Message::SetTab(tabs[i.min(tabs.len() - 1)]),
            |_| Message::Noop,
            0.0,
            false,
            tea,
            A11y::new("panes", Role::Tab),
        );
    }

    let mut r = row![].spacing(0).align_y(Alignment::End);
    for (i, tab) in tabs.iter().enumerate() {
        let title = tab.label().to_string();
        let enabled = *tab == Tab::Overview;
        let show_active = enabled && i == active_i;
        let label = if enabled {
            text(title.clone()).size(typo::META)
        } else {
            text(title.clone()).size(typo::META).color(tea.muted)
        };
        let mut btn = iced::widget::button(label)
            .padding([12, 16])
            .style(icedtea::style::tab_style(tea, show_active));
        if enabled {
            btn = btn.on_press(Message::SetTab(*tab));
        }
        let bar_h = if show_active { 3.0_f32 } else { 0.0_f32 };
        let indicator = container(Space::new().height(bar_h))
            .width(Length::Fill)
            .style(move |_| {
                if show_active {
                    icedtea::style::tab_indicator(tea)
                } else {
                    icedtea::style::fill(iced::Color::TRANSPARENT, tea.text)
                }
            });
        let cell = column![btn, indicator].spacing(0).width(Length::Shrink);
        r = r.push(icedtea::a11y::attach(
            cell.into(),
            &A11y::new(title, Role::Tab).with_checked(show_active),
        ));
    }
    let strip = column![
        r,
        container(Space::new().width(Length::Fill).height(1))
            .style(move |_| icedtea::style::hairline(tea)),
    ];
    icedtea::a11y::attach(
        container(strip)
            .width(Length::Fill)
            .style(move |_| icedtea::style::app_bar(tea))
            .into(),
        &A11y::new("panes", Role::Tab),
    )
}

/// Labeled copyable value — icedtea [`widget::value_field`] with FORM_LABEL gutter.
pub fn labeled_value<'a>(
    title: &str,
    content: &'a iced::widget::text_editor::Content,
    on_action: impl Fn(iced::widget::text_editor::Action) -> Message + 'a,
    face: FontFace,
    tea: Tokens,
    a11y: A11y,
) -> Element<'a, Message> {
    widget::value_field(
        title,
        content,
        on_action,
        None,
        face,
        LABEL_GUTTER,
        tea,
        Direction::Ltr,
        a11y,
    )
}

/// Non-copyable labeled readout via icedtea [`layout::form`] (same gutter).
pub fn labeled_plain<'a>(
    title: &str,
    value: impl Into<String>,
    tea: Tokens,
) -> Element<'a, Message> {
    let value = value.into();
    icedtea::layout::form(
        [(
            widget::meta(title.to_string(), tea, A11y::new(title, Role::Status)),
            text(value).size(typo::BODY).color(tea.text).into(),
        )],
        8,
        Direction::Ltr,
    )
}

/// Footer — icedtea [`pattern::status_bar`] + [`ActionTable::footer_hints`].
pub fn status_footer<'a>(
    status: &str,
    err: bool,
    table: &icedtea::action::ActionTable<Message>,
    tea: Tokens,
) -> Element<'a, Message> {
    let tone = if err { Some(ToastKind::Danger) } else { None };
    icedtea::pattern::status_bar(status.to_string(), tone, None, table, tea, Direction::Ltr)
}

/// `?` help sheet: icedtea [`pattern::cheatsheet`] in a modal card.
pub fn help_modal<'a>(
    backdrop: Element<'a, Message>,
    table: &icedtea::action::ActionTable<Message>,
    tea: Tokens,
) -> Element<'a, Message> {
    let sheet = widget::group_box(
        "Keyboard shortcuts",
        icedtea::pattern::cheatsheet(table, "", tea),
        tea,
        widget::CardFace::Elevated,
        A11y::new("Keyboard shortcuts", Role::Dialog),
    );
    let card = container(sheet)
        .width(Length::Fixed(520.0))
        .height(Length::Fixed(400.0));
    icedtea::pattern::modal_card(backdrop, card.into(), tea)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn label_gutter_matches_icedtea_form_label() {
        assert!((LABEL_GUTTER - icedtea::layout::FORM_LABEL).abs() < f32::EPSILON);
        const { assert!(LABEL_GUTTER >= 96.0) };
    }

    #[test]
    fn status_empty_builds() {
        let tea = icedtea::theme::named("dark").tokens;
        let _ = status_empty("No turns", "Nothing segmented yet.", tea);
    }

    #[test]
    fn context_progress_builds() {
        let tea = icedtea::theme::named("dark").tokens;
        let _ = context_progress(0.42, tea);
        let _ = context_progress(0.0, tea);
        let _ = context_progress(1.0, tea);
    }

    #[test]
    fn pane_tabs_ready_uses_tab_bar_path() {
        let tea = icedtea::theme::named("dark").tokens;
        let _ = pane_tabs(Tab::Overview, true, &Tab::ALL, tea);
        let _ = pane_tabs(Tab::Timeline, false, &Tab::ALL, tea);
    }

    #[test]
    fn search_field_builds() {
        let tea = icedtea::theme::named("dark").tokens;
        let _ = search_field(
            "Search sessions",
            "q",
            Message::SearchChanged,
            Some(Message::ActivateSelected),
            tea,
            A11y::new("Search sessions", Role::TextBox),
            None,
        );
    }

    #[test]
    fn status_footer_builds() {
        let tea = icedtea::theme::named("dark").tokens;
        let table = crate::help::footer_table(crate::help::KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: true,
            awaiting: false,
            child_open: false,
            compact_child: false,
            turn_pick: true,
            tab: crate::model::Tab::Timeline,
            leader_armed: false,
        });
        let _ = status_footer("ready", false, &table, tea);
        let _ = status_footer(
            "down",
            true,
            &crate::help::footer_table(crate::help::KeyScope {
                browse: false,
                help_open: false,
                timeline_detail: false,
                awaiting: false,
                child_open: false,
                compact_child: false,
                turn_pick: false,
                tab: crate::model::Tab::Overview,
                leader_armed: false,
            }),
            tea,
        );
    }

    #[test]
    fn help_modal_builds() {
        let tea = icedtea::theme::named("dark").tokens;
        let table = crate::help::help_table(crate::help::KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            awaiting: false,
            child_open: false,
            compact_child: false,
            turn_pick: true,
            tab: crate::model::Tab::Overview,
        });
        let backdrop = status_empty("HUD", "backdrop", tea);
        let _ = help_modal(backdrop, &table, tea);
    }

    #[test]
    fn kit_uses_icedtea_constructors() {
        let src = include_str!("kit.rs");
        assert!(src.contains("widget::value_field"));
        assert!(src.contains("widget::progress"));
        assert!(src.contains("widget::tab_bar"));
        assert!(src.contains("widget::FieldOpts"));
        assert!(src.contains("Icons::leading"));
        assert!(src.contains("pattern::status_bar"));
        assert!(src.contains("pattern::status_page"));
        assert!(src.contains("pattern::modal_card"));
        assert!(src.contains("pattern::cheatsheet"));
        assert!(src.contains("layout::form"));
        assert!(src.contains("style::tab_indicator"));
        assert!(src.contains("style::app_bar"));
        assert!(src.contains("FORM_LABEL"));
    }
}

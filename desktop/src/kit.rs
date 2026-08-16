//! HUD chrome built on icedtea constructors.
//!
//! Prefer icedtea public APIs directly. Helpers here exist only when icedtea
//! is missing a parameter (per-tab disable, custom search placeholder/submit,
//! compact select size).

use std::rc::Rc;

use iced::widget::{column, container, mouse_area, pick_list, row, text, Space};
use iced::{Alignment, Element, Length, Padding};
use icedtea::a11y::{A11y, Role};
use icedtea::collection::Tabs;
use icedtea::i18n::Direction;
use icedtea::theme::Tokens;
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

/// Compact select. icedtea [`widget::themed_pick_list`] is a form field
/// (body type + density pad) and has no size parameter.
pub fn compact_pick<'a, T, M: Clone + 'a>(
    options: impl std::borrow::Borrow<[T]> + 'a,
    selected: Option<T>,
    on_select: impl Fn(T) -> M + 'a,
    tea: Tokens,
    a11y: A11y,
) -> Element<'a, M>
where
    T: ToString + PartialEq + Clone + 'a,
{
    const PAD: Padding = Padding {
        top: 4.0,
        right: 8.0,
        bottom: 4.0,
        left: 8.0,
    };
    if a11y.disabled {
        let _ = on_select;
        let shown = selected
            .as_ref()
            .map(ToString::to_string)
            .unwrap_or_default();
        return icedtea::a11y::attach(
            container(text(shown).size(typo::META).color(tea.muted))
                .padding(PAD)
                .into(),
            &a11y,
        );
    }
    let opts: Vec<T> = options.borrow().to_vec();
    let sel = selected.clone();
    let on_select = Rc::new(on_select);
    let on_pick = {
        let on_select = on_select.clone();
        move |t| on_select(t)
    };
    let picker = pick_list(options, selected, on_pick)
        .style(icedtea::style::picker_style(tea))
        .text_size(typo::META)
        .padding(PAD);
    let el: Element<'a, M> = if opts.is_empty() {
        picker.into()
    } else {
        mouse_area(picker)
            .on_scroll(move |delta| {
                let n = opts.len();
                let i = sel
                    .as_ref()
                    .and_then(|s| opts.iter().position(|o| o == s))
                    .unwrap_or(0);
                let j = if widget::scroll_wheel_y(delta) < 0.0 {
                    i.saturating_add(1).min(n - 1)
                } else {
                    i.saturating_sub(1)
                };
                on_select(opts[j].clone())
            })
            .into()
    };
    icedtea::a11y::attach(el, &a11y)
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

/// Footer — shortcut hints that apply right now.
pub fn status_footer<'a>(
    table: &icedtea::action::ActionTable<Message>,
    tea: Tokens,
) -> Element<'a, Message> {
    let keys = table.footer_hints().join("  ·  ");
    let key_row: Element<'a, Message> = if keys.is_empty() {
        Space::new().height(0).into()
    } else {
        widget::meta(keys.clone(), tea, A11y::new(keys, Role::Status))
    };
    icedtea::a11y::attach(
        container(
            column![key_row]
                .width(Length::Fill)
                .padding(Padding::from([6, 12])),
        )
        .width(Length::Fill)
        .style(move |_| icedtea::style::footer(tea))
        .into(),
        &A11y::new("statusbar", Role::Status),
    )
}

/// `?` help sheet: shortcut rows in a modal, with right pad for the scroll rail.
///
/// icedtea [`pattern::cheatsheet`] paints the rail over the key names.
pub fn help_modal<'a>(
    backdrop: Element<'a, Message>,
    table: &icedtea::action::ActionTable<Message>,
    tea: Tokens,
) -> Element<'a, Message> {
    let heading = format!("Keyboard shortcuts · groket {}", crate::VERSION);
    let rail = icedtea::chrome::SCROLL_RAIL_WIDTH;
    let mut rows = column![].spacing(4).padding(Padding {
        top: 8.0,
        right: 8.0 + rail,
        bottom: 8.0,
        left: 8.0,
    });
    for a in table.iter() {
        if !a.enabled {
            continue;
        }
        let keys = a
            .tooltip
            .clone()
            .filter(|t| !t.is_empty())
            .or_else(|| a.shortcut.as_ref().map(ToString::to_string))
            .unwrap_or_else(|| "—".into());
        rows = rows.push(row![
            widget::label(
                a.title.clone(),
                tea,
                A11y::new(a.title.clone(), Role::Status),
            ),
            Space::new().width(Length::Fill),
            widget::meta(keys.clone(), tea, A11y::new(keys, Role::Status)),
        ]);
    }
    let list = widget::themed_scroll(
        rows.into(),
        tea,
        A11y::new("cheatsheet", Role::Group),
        false,
        None,
        None::<fn(_) -> Message>,
    );
    let sheet = widget::group_box(
        heading.clone(),
        list,
        tea,
        widget::CardFace::Elevated,
        A11y::new(heading, Role::Dialog),
    );
    let card = container(sheet)
        .width(Length::Fixed(520.0))
        .height(Length::Fixed(400.0));
    icedtea::pattern::modal_card(backdrop, card.into(), 1.0, tea)
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
    fn compact_pick_builds() {
        let tea = icedtea::theme::named("dark").tokens;
        let _ = compact_pick(
            &["All", "Tools"][..],
            Some("All"),
            |_| Message::Noop,
            tea,
            A11y::new("Filter", Role::ComboBox),
        );
        let _ = compact_pick(
            &["All"][..],
            Some("All"),
            |_| Message::Noop,
            tea,
            A11y::new("Filter", Role::ComboBox).with_disabled(true),
        );
        let _ = compact_pick(
            &[] as &[&str],
            None,
            |_| Message::Noop,
            tea,
            A11y::new("empty", Role::ComboBox),
        );
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
            diff_pick: false,
            tab: crate::model::Tab::Timeline,
            leader_armed: false,
        });
        let _ = status_footer(&table, tea);
        let _ = status_footer(
            &crate::help::footer_table(crate::help::KeyScope {
                browse: false,
                help_open: false,
                timeline_detail: false,
                awaiting: false,
                child_open: false,
                compact_child: false,
                turn_pick: false,
                diff_pick: false,
                tab: crate::model::Tab::Overview,
                leader_armed: false,
            }),
            tea,
        );
        let src = include_str!("kit.rs");
        let prod = src.split("#[cfg(test)]").next().expect("prod");
        let body = prod
            .split("fn status_footer")
            .nth(1)
            .expect("status_footer")
            .split("pub fn help_modal")
            .next()
            .expect("body");
        assert!(body.contains("column![key_row]"));
        assert!(!body.contains("status_bar"));
        assert!(!body.contains("info_bar"));
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
            diff_pick: false,
            tab: crate::model::Tab::Overview,
            leader_armed: false,
        });
        let backdrop = status_empty("HUD", "backdrop", tea);
        let _ = help_modal(backdrop, &table, tea);
    }

    #[test]
    fn help_modal_title_includes_product_version() {
        let src = include_str!("kit.rs");
        assert!(src.contains("crate::VERSION"));
    }

    #[test]
    fn help_sheet_pads_for_the_scroll_rail() {
        let src = include_str!("kit.rs");
        let help = src
            .split("pub fn help_modal")
            .nth(1)
            .unwrap()
            .split("#[cfg(test)]")
            .next()
            .unwrap();
        assert!(help.contains("SCROLL_RAIL_WIDTH"));
        assert!(help.contains("right: 8.0 + rail"));
        assert!(help.contains("tooltip"));
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
        assert!(src.contains("SCROLL_RAIL_WIDTH"));
        assert!(src.contains("layout::form"));
        assert!(src.contains("style::tab_indicator"));
        assert!(src.contains("style::app_bar"));
        assert!(src.contains("FORM_LABEL"));
    }
}

//! Palette layout.

use std::cell::RefCell;
use std::collections::HashMap;
use std::hash::{Hash, Hasher};

use iced::mouse;
use iced::widget::canvas::{self, Canvas};
use iced::widget::{
    button, column, container, image, markdown, mouse_area, pick_list, responsive, rich_text, row,
    scrollable, text, text_input, Space,
};
use iced::{Alignment, Color, Element, Length, Padding, Point, Rectangle, Renderer, Size, Theme};
use serde_json::json;

use crate::app::{Hud, Message};
use crate::brand;
use crate::format::{
    capped_display, capped_json, event_role, format_note_time, list_status_label, looks_like_json,
    looks_like_markdown, note_fields_view, origin_label, pretty_json, sanitize_console_text,
    status_tone, timeline_body_text, EventRole,
};
use crate::live::{
    visible_range_covering, wheel_scroll, CardMark, LIST_ROW_H, TIMELINE_ROW_H, VIRT_OVERSCAN,
};
use crate::model::{KindFilter, Tab};
use crate::scroll::ScrollRail;
use crate::style;
use crate::typo;
use crate::wire::{FindingRow, NoteRow, TimelineEvent, TurnRow};

#[allow(clippy::too_many_arguments)]
fn virt_pane<'a>(
    body: Element<'a, Message>,
    content: f32,
    viewport: f32,
    scroll: f32,
    row_h: f32,
    on_scroll: impl Fn(f32) -> Message + Copy + 'a,
    tok: crate::theme::Tokens,
) -> Element<'a, Message> {
    let max = (content - viewport).max(0.0);
    let body = mouse_area(body).on_scroll(move |d| on_scroll(wheel_scroll(d, scroll, row_h, max)));
    if content <= viewport {
        return body.into();
    }
    row![
        body,
        Element::from(ScrollRail::new(content, viewport, scroll, on_scroll, tok)),
    ]
    .height(Length::Fill)
    .into()
}

fn rule(tok: crate::theme::Tokens) -> Element<'static, Message> {
    container(Space::with_height(1))
        .width(Length::Fill)
        .height(1)
        .style(move |_| style::hairline(tok))
        .into()
}

pub fn layout(hud: &Hud) -> Element<'_, Message> {
    let tok = hud.tokens();
    let mut search = row![
        image(brand::chrome_handle(tok.canvas_is_dark()))
            .width(brand::chrome_width())
            .height(brand::chrome_height()),
        text_input("Search sessions", hud.query())
            .id(hud.search_id())
            .font(typo::UI)
            .size(typo::BODY)
            .on_input(Message::SearchChanged)
            .padding([8, 10])
            .style(style::search(tok))
            .width(Length::Fill),
        text(hud.hotkey_hint())
            .size(typo::META)
            .font(typo::MONO)
            .color(tok.muted),
    ]
    .spacing(12)
    .align_y(Alignment::Center);
    if !hud.window_mode() {
        search = search.push(pop_out_control(tok));
    }
    let search = search.padding(Padding::from([12, 16]));

    let list = session_list(hud);
    let detail = detail_pane(hud);

    let body = row![list, detail].spacing(0).height(Length::Fill);

    let foot = row![
        {
            let t = text(hud.status()).size(typo::META).font(typo::UI);
            if hud.status_err() {
                t.color(tok.error)
            } else {
                t.color(tok.muted)
            }
        },
        Space::with_width(Length::Fill),
        text("Up/Down list  ·  Tab fields  ·  Ctrl+1–5 panes  ·  / events  ·  Esc")
            .size(typo::META)
            .color(tok.muted),
    ]
    .spacing(12)
    .align_y(Alignment::Center)
    .padding(Padding::from([8, 16]));
    let foot = container(foot)
        .width(Length::Fill)
        .style(move |_| style::footer(tok));

    container(column![search, rule(tok), body, rule(tok), foot])
        .width(Length::Fill)
        .height(Length::Fill)
        .padding(1)
        .style(move |_| style::shell(tok))
        .into()
}

fn session_list(hud: &Hud) -> Element<'_, Message> {
    let tok = hud.tokens();
    container(responsive(move |size| {
        session_list_at(hud, size.height.max(1.0))
    }))
    .width(Length::Fixed(260.0))
    .height(Length::Fill)
    .style(move |_| style::fill(tok.panel, tok.text))
    .into()
}

fn session_list_at(hud: &Hud, viewport: f32) -> Element<'_, Message> {
    let tok = hud.tokens();
    let n = hud.sessions().len();
    let mut col = column![].spacing(0).padding(8);
    if n == 0 {
        col = col.push(text("No sessions").size(typo::BODY).color(tok.muted));
        return col.into();
    }
    let win = visible_range_covering(
        hud.list_scroll_y(),
        viewport,
        LIST_ROW_H,
        n,
        VIRT_OVERSCAN,
        Some(hud.active()),
    );
    if win.pad_top > 0.0 {
        col = col.push(Space::with_height(win.pad_top));
    }
    for (i, row) in hud.sessions()[win.start..win.end].iter().enumerate() {
        let i = win.start + i;
        let selected = i == hud.active();
        let status = list_status_label(&row.status, &row.outcome);
        let tone = status_tone(&status);
        let sub = format!(
            "{} · {}{}",
            status,
            row.model,
            if row.context_usage_compact.is_empty() {
                String::new()
            } else {
                format!(" · {}", row.context_usage_compact)
            }
        );
        let title_font = if selected { typo::UI_BOLD } else { typo::UI };
        let title_c = tok.text;
        let sub_c = tone_color(tone, tok);
        let label = column![
            text(row.display_title())
                .size(typo::BODY)
                .font(title_font)
                .color(title_c),
            text(sub).size(typo::META).color(sub_c),
        ]
        .spacing(4);
        col = col.push(
            mouse_area(
                container(label)
                    .width(Length::Fill)
                    .padding([10, 12])
                    .style(move |_| style::list_row(tok, selected)),
            )
            .on_press(Message::SelectSession(i)),
        );
    }
    if win.pad_bottom > 0.0 {
        col = col.push(Space::with_height(win.pad_bottom));
    }
    let scroll = hud.list_scroll_y();
    virt_pane(
        col.into(),
        n as f32 * LIST_ROW_H,
        viewport,
        scroll,
        LIST_ROW_H,
        move |y| Message::ListScroll {
            y,
            height: viewport,
        },
        tok,
    )
}

fn detail_pane(hud: &Hud) -> Element<'_, Message> {
    let tabs = row(Tab::ALL.iter().copied().map(|t| {
        button(text(t.label()).size(typo::META))
            .on_press(Message::SetTab(t))
            .padding([8, 12])
            .style(style::tab(hud.tokens(), hud.tab() == t))
            .into()
    }))
    .spacing(4)
    .padding(Padding::from([8, 12]));

    let mut stack = column![tabs].spacing(0).height(Length::Fill);
    if hud.tab() == Tab::Timeline && !hud.overview_sid().is_empty() {
        stack = stack.push(timeline_filter(hud));
    }
    let body: Element<'_, Message> = if hud.overview().is_none() {
        if !hud.overview_pending().is_empty() {
            text(format!("Loading {}…", hud.overview_pending()))
                .size(typo::BODY)
                .color(hud.tokens().muted)
                .into()
        } else {
            text("Select a session")
                .size(typo::BODY)
                .color(hud.tokens().muted)
                .into()
        }
    } else {
        match hud.tab() {
            Tab::Overview => overview_tab(hud),
            Tab::Turns => turns_tab(hud),
            Tab::Timeline => column![].into(),
            Tab::Findings => findings_tab(hud),
            Tab::Notes => notes_tab(hud),
        }
    };
    if hud.tab() == Tab::Timeline && hud.overview().is_some() {
        stack = stack.push(responsive(move |size| {
            let viewport = size.height.max(1.0);
            let n = hud.filtered_timeline().len();
            virt_pane(
                container(timeline_tab(hud, viewport))
                    .padding([16, 20])
                    .width(Length::Fill)
                    .into(),
                n as f32 * TIMELINE_ROW_H,
                viewport,
                hud.tl_scroll_y(),
                TIMELINE_ROW_H,
                move |y| Message::TimelineScroll {
                    y,
                    height: viewport,
                },
                hud.tokens(),
            )
        }));
    } else {
        stack = stack.push(
            scrollable(container(body).padding([16, 20]).width(Length::Fill))
                .height(Length::Fill)
                .on_scroll(|vp| Message::TimelineScroll {
                    y: vp.absolute_offset().y,
                    height: vp.bounds().height,
                }),
        );
    }
    container(stack)
        .width(Length::Fill)
        .height(Length::Fill)
        .into()
}

fn timeline_filter(hud: &Hud) -> Element<'_, Message> {
    let tok = hud.tokens();
    row![
        text("Type").size(typo::META).color(tok.muted),
        pick_list(
            &KindFilter::ALL[..],
            Some(hud.timeline_kind()),
            Message::TimelineKind
        )
        .padding([6, 8])
        .text_size(typo::META)
        .font(typo::UI)
        .style(style::picker(tok)),
        text_input("Filter events", hud.timeline_query())
            .id(hud.tl_search_id())
            .font(typo::UI)
            .size(typo::BODY)
            .on_input(Message::TimelineQuery)
            .padding(8)
            .style(style::search(tok))
            .width(Length::Fill),
        text(hud.timeline_meta()).size(typo::META).color(tok.muted),
    ]
    .spacing(8)
    .align_y(Alignment::Center)
    .padding(Padding::from([8, 12]))
    .into()
}

fn overview_tab(hud: &Hud) -> Element<'static, Message> {
    let o = hud.overview().unwrap();
    let meta = &o.meta;
    let mut title = meta.title.clone();
    if title.is_empty() {
        title = hud.overview_sid().to_string();
    }
    let mut summary = o.summary.clone();
    if summary.is_empty() {
        summary = meta.summary.clone();
    }
    if summary.is_empty() {
        summary = "No summary text for this session.".into();
    }
    let ctx = meta.context_compact().to_string();
    let status = meta.status_label();
    let tone = status_tone(&status);
    let hero = format!(
        "{} · {} · {} · {}",
        meta.model,
        origin_label(&meta.origin),
        meta.duration,
        ctx,
    );
    let id = meta.session_id.clone();
    let events = meta.num_events.to_string();
    let tools = format!("{} ({} errors)", meta.tool_call_count, meta.error_count);
    let turns_n = o.turns.total.to_string();
    let findings_n = if o.findings.total > 0 {
        o.findings.total.to_string()
    } else {
        o.findings.count.to_string()
    };
    let notes_n = o.notes.count.to_string();
    let git = {
        let repo = meta.git_repo.clone();
        let branch = meta.git_branch.clone();
        match (repo.is_empty(), branch.is_empty()) {
            (true, true) => "—".into(),
            (false, true) => repo,
            (true, false) => branch,
            (false, false) => format!("{repo} · {branch}"),
        }
    };
    let path = meta.path.clone();
    let tok = hud.tokens();
    let mut col = column![
        text(title.clone())
            .size(typo::PAGE)
            .font(typo::UI_BOLD)
            .color(tok.text),
        row![
            text(if status.is_empty() {
                "—".into()
            } else {
                status
            })
            .size(typo::META)
            .font(typo::UI_BOLD)
            .color(tone_color(tone, tok)),
            text(format!(" · {hero}")).size(typo::META).color(tok.muted),
        ]
        .spacing(0),
    ]
    .spacing(8);
    if summary != title && summary != "No summary text for this session." {
        col = col.push(md_body(&summary, 4000, typo::BODY, md_style(hud)));
    } else if summary == "No summary text for this session." {
        col = col.push(text(summary).size(typo::BODY).color(tok.muted));
    }
    col.push(kv(hud, "session", id, true))
        .push(kv(hud, "events", events, false))
        .push(kv(hud, "tools", tools, false))
        .push(kv(hud, "turns", turns_n, false))
        .push(kv(hud, "findings", findings_n, false))
        .push(kv(hud, "notes", notes_n, false))
        .push(kv(hud, "git", git, false))
        .push(kv(hud, "path", path, true))
        .into()
}

fn md_style(hud: &Hud) -> markdown::Style {
    let tok = hud.tokens();
    let mut style =
        markdown::Style::from_palette(crate::theme::iced_theme(hud.theme_name()).palette());
    style.inline_code_color = tok.accent;
    style.link_color = tok.primary;
    style
}

thread_local! {
    static MD_CACHE: RefCell<HashMap<u64, Vec<markdown::Item>>> = RefCell::new(HashMap::new());
}

fn md_key(src: &str, max_chars: usize) -> u64 {
    let mut h = std::collections::hash_map::DefaultHasher::new();
    max_chars.hash(&mut h);
    src.hash(&mut h);
    h.finish()
}

fn md_body(
    src: &str,
    max_chars: usize,
    size: u16,
    style: markdown::Style,
) -> Element<'static, Message> {
    let cut: String = src.chars().take(max_chars).collect();
    if cut.trim().is_empty() {
        return Space::with_height(0).into();
    }
    if !looks_like_markdown(&cut) {
        return text(cut).size(size).font(typo::UI).into();
    }
    md_parsed(&cut, max_chars, size, style)
}

fn md_parsed(
    src: &str,
    max_chars: usize,
    size: u16,
    style: markdown::Style,
) -> Element<'static, Message> {
    let key = md_key(src, max_chars);
    MD_CACHE.with(|cache| {
        let mut cache = cache.borrow_mut();
        if cache.len() > 80 {
            cache.clear();
        }
        let items = cache
            .entry(key)
            .or_insert_with(|| markdown::parse(src).collect());
        md_column(items, markdown::Settings::with_text_size(size), style)
    })
}

fn md_column(
    items: &[markdown::Item],
    settings: markdown::Settings,
    style: markdown::Style,
) -> Element<'static, Message> {
    let children: Vec<Element<'static, Message>> = items
        .iter()
        .map(|item| md_item(item, settings, style))
        .collect();
    column(children)
        .spacing(settings.text_size.0 * 0.4)
        .width(Length::Fill)
        .into()
}

fn md_item(
    item: &markdown::Item,
    settings: markdown::Settings,
    style: markdown::Style,
) -> Element<'static, Message> {
    let text_size = settings.text_size;
    match item {
        markdown::Item::Heading(level, heading) => {
            let size = match level {
                markdown::HeadingLevel::H1 => settings.h1_size,
                markdown::HeadingLevel::H2 => settings.h2_size,
                markdown::HeadingLevel::H3 => settings.h3_size,
                markdown::HeadingLevel::H4 => settings.h4_size,
                markdown::HeadingLevel::H5 => settings.h5_size,
                markdown::HeadingLevel::H6 => settings.h6_size,
            };
            Element::<markdown::Url>::from(rich_text(heading.spans(style)).size(size))
                .map(|url| Message::MdLink(url.to_string()))
        }
        markdown::Item::Paragraph(paragraph) => {
            Element::<markdown::Url>::from(rich_text(paragraph.spans(style)).size(text_size))
                .map(|url| Message::MdLink(url.to_string()))
        }
        markdown::Item::CodeBlock(code) => container(
            Element::<markdown::Url>::from(
                rich_text(code.spans(style))
                    .font(typo::MONO)
                    .size(settings.code_size),
            )
            .map(|url| Message::MdLink(url.to_string())),
        )
        .padding(6)
        .width(Length::Fill)
        .into(),
        markdown::Item::List { start, items } => {
            let rows: Vec<Element<'static, Message>> = items
                .iter()
                .enumerate()
                .map(|(i, nested)| {
                    let bullet = match start {
                        Some(n) => format!("{}.", *n + i as u64),
                        None => "•".into(),
                    };
                    row![
                        text(bullet).size(text_size),
                        md_column(nested, settings, style),
                    ]
                    .spacing(8)
                    .into()
                })
                .collect();
            column(rows).spacing(text_size.0 * 0.3).into()
        }
    }
}

fn kv(hud: &Hud, k: &'static str, v: String, mono: bool) -> Element<'static, Message> {
    let tok = hud.tokens();
    let val = if mono {
        text(v).size(typo::META).font(typo::MONO).color(tok.text)
    } else {
        text(v).size(typo::BODY).font(typo::UI).color(tok.text)
    };
    row![
        text(k)
            .size(typo::META)
            .color(tok.muted)
            .width(Length::Fixed(80.0)),
        val
    ]
    .spacing(8)
    .into()
}

fn marks_row(
    hud: &Hud,
    mark: Option<CardMark>,
    turn: String,
    event: String,
) -> Element<'static, Message> {
    let tok = hud.tokens();
    let mut r = row![].spacing(4);
    if let Some(m) = mark {
        if m.findings > 0 {
            let ev = m.first_finding_event;
            r = r.push(
                button(text(format!("f{}", m.findings)).size(typo::META))
                    .on_press(if let Some(ix) = ev {
                        Message::JumpTimeline(ix)
                    } else {
                        Message::SetTab(Tab::Findings)
                    })
                    .padding([3, 6])
                    .style(style::chip(tok)),
            );
        }
        if m.notes > 0 {
            let nid = m.first_note_id;
            r = r.push(
                button(text(format!("n{}", m.notes)).size(typo::META))
                    .on_press(if nid.is_empty() {
                        Message::SetTab(Tab::Notes)
                    } else {
                        Message::OpenNote(nid)
                    })
                    .padding([3, 6])
                    .style(style::chip(tok)),
            );
        }
        if m.errors > 0 {
            r = r.push(
                text(format!("e{}", m.errors))
                    .size(typo::META)
                    .color(tok.error),
            );
        }
    }
    r = r.push(
        button(text("Add note").size(typo::META))
            .on_press(Message::StartNote { turn, event })
            .padding([3, 6])
            .style(style::chip(tok)),
    );
    r.into()
}

fn turns_tab(hud: &Hud) -> Element<'_, Message> {
    let o = hud.overview().unwrap();
    let turns: &[TurnRow] = &o.turns.turns;
    let (turn_marks, _) = hud.card_marks();
    if turns.is_empty() {
        return text("No turns segmented.")
            .size(typo::BODY)
            .color(hud.tokens().muted)
            .into();
    }
    let mut col = column![].spacing(12);
    for t in turns {
        let idx = t.turn_index;
        let jump = t.user_event_index.or(t.first_index);
        let summary = t.summary.clone();
        let assistant = t.assistant_summary.clone();
        let assistant_jump = t.assistant_event_index;
        let label = if t.label.is_empty() {
            format!("turn {idx}")
        } else {
            t.label.clone()
        };
        let meta = format!(
            "prompt {} · events {} · tools {} ({} errors){}{} · index {}–{}",
            t.prompt_index
                .map(|n| n.to_string())
                .unwrap_or_else(|| "—".into()),
            t.event_count,
            t.tool_call_count,
            t.tool_error_count,
            if t.outcome.is_empty() {
                String::new()
            } else {
                format!(" · {}", t.outcome)
            },
            if t.open { " · open" } else { "" },
            t.first_index
                .map(|n| n.to_string())
                .unwrap_or_else(|| "—".into()),
            t.last_index
                .map(|n| n.to_string())
                .unwrap_or_else(|| "—".into()),
        );
        let head = row![
            button(text(label).size(typo::TITLE).font(typo::UI_BOLD))
                .on_press(
                    jump.map(Message::JumpTimeline)
                        .unwrap_or(Message::SetTab(Tab::Timeline))
                )
                .padding([4, 8])
                .style(style::quiet(hud.tokens())),
            Space::with_width(Length::Fill),
            marks_row(
                hud,
                turn_marks.get(&idx).cloned(),
                idx.to_string(),
                String::new()
            ),
        ]
        .align_y(Alignment::Center);
        let mut body = column![].spacing(8);
        body = body.push(text("User").size(typo::META).color(hud.tokens().muted));
        if summary.is_empty() {
            body = body.push(
                text("No user prompt in this turn")
                    .size(typo::BODY)
                    .color(hud.tokens().muted),
            );
        } else {
            body = body.push(
                text(sanitize_console_text(&summary))
                    .size(typo::BODY)
                    .font(typo::UI),
            );
        }
        if !assistant.is_empty() {
            let asst_head: Element<'_, Message> = if let Some(ix) = assistant_jump {
                button(text("Assistant").size(typo::META))
                    .on_press(Message::JumpTimeline(ix))
                    .padding([2, 0])
                    .style(style::quiet(hud.tokens()))
                    .into()
            } else {
                text("Assistant")
                    .size(typo::META)
                    .color(hud.tokens().muted)
                    .into()
            };
            body = body.push(asst_head);
            body = body.push(
                text(sanitize_console_text(&assistant))
                    .size(typo::BODY)
                    .font(typo::UI),
            );
        }
        col = col.push(
            container(
                column![
                    head,
                    body,
                    text(meta).size(typo::META).color(hud.tokens().muted)
                ]
                .spacing(10),
            )
            .padding(16)
            .style(move |_| style::card(hud.tokens(), false)),
        );
    }
    col.into()
}

fn timeline_tab(hud: &Hud, viewport: f32) -> Element<'_, Message> {
    if hud.timeline_loading() && hud.filtered_timeline().is_empty() {
        return text("Loading timeline…")
            .size(typo::BODY)
            .color(hud.tokens().muted)
            .into();
    }
    let events = hud.filtered_timeline();
    if events.is_empty() {
        if hud.timeline_loading() || !hud.timeline_complete() {
            return text("Loading matching events…")
                .size(typo::BODY)
                .color(hud.tokens().muted)
                .into();
        }
        return text("No events in this filter.")
            .size(typo::BODY)
            .color(hud.tokens().muted)
            .into();
    }
    let (_, ev_marks) = hud.card_marks();
    let focus = hud.timeline_focus();
    let n = events.len();
    let win = visible_range_covering(
        hud.tl_scroll_y(),
        viewport,
        TIMELINE_ROW_H,
        n,
        VIRT_OVERSCAN,
        hud.timeline_focus_pos(),
    );
    let start = win.start.min(n);
    let end = win.end.min(n).max(start);
    let mut col = column![].spacing(0);
    if win.pad_top > 0.0 {
        col = col.push(Space::with_height(win.pad_top));
    }
    for ev in events[start..end].iter().copied() {
        let ix = ev.index;
        let turn = ev.turn_index.map(|n| n.to_string()).unwrap_or_default();
        let heading = if ev.heading.is_empty() {
            ev.type_label.clone()
        } else {
            ev.heading.clone()
        };
        let selected = focus == Some(ix);
        let kind = ev.kind.clone();
        let type_label = if ev.type_label.is_empty() {
            kind.clone()
        } else {
            ev.type_label.clone()
        };
        let is_error = ev.is_error;
        let type_color = role_color(event_role(&kind, is_error), hud.tokens());
        let idx_s = ev.index.to_string();
        let time_s = ev.time.clone();
        let tok = hud.tokens();
        let head = row![
            container(
                text(idx_s)
                    .size(typo::META)
                    .font(typo::MONO)
                    .color(tok.muted)
            )
            .width(Length::Fixed(56.0)),
            container(
                text(type_label)
                    .size(typo::META)
                    .font(typo::UI_BOLD)
                    .color(type_color)
            )
            .width(Length::FillPortion(3)),
            container(
                text(time_s)
                    .size(typo::META)
                    .font(typo::MONO)
                    .color(tok.muted)
            )
            .width(Length::FillPortion(2)),
            marks_row(hud, ev_marks.get(&ix).cloned(), turn, ix.to_string(),),
        ]
        .spacing(12)
        .align_y(Alignment::Center);
        let mut card = column![
            head,
            text(heading)
                .size(typo::TITLE)
                .font(typo::UI_BOLD)
                .color(hud.tokens().text)
        ]
        .spacing(8);
        card = card.push(event_payload(ev, selected, hud));
        card = card.push(
            text(if selected {
                "Click to collapse"
            } else {
                "Click to expand"
            })
            .size(typo::META)
            .color(tok.muted),
        );
        if selected && ev.content_truncated {
            card = card.push(
                text("Content truncated by control")
                    .size(typo::META)
                    .color(tok.muted),
            );
        }
        let ix_click = ix;
        col = col.push(
            mouse_area(
                container(card)
                    .padding(16)
                    .width(Length::Fill)
                    .style(move |_| style::card(hud.tokens(), selected)),
            )
            .on_press(Message::SelectTimeline(ix_click)),
        );
    }
    if win.pad_bottom > 0.0 {
        col = col.push(Space::with_height(win.pad_bottom));
    }
    if !hud.timeline_complete() {
        col = col.push(
            text(if hud.timeline_loading() {
                "Loading more events…"
            } else {
                "More events available — scroll or wait"
            })
            .size(typo::META)
            .color(hud.tokens().muted),
        );
    }
    col.into()
}

fn findings_tab(hud: &Hud) -> Element<'_, Message> {
    let o = hud.overview().unwrap();
    let findings: &[FindingRow] = &o.findings.findings;
    if findings.is_empty() {
        return column![
            text("No analysis findings in cache for this session.")
                .size(typo::BODY)
                .color(hud.tokens().muted),
            text("Run analysis in the TUI so results land under ~/.groket/cache/analysis.")
                .size(typo::META)
                .color(hud.tokens().muted),
        ]
        .spacing(6)
        .into();
    }
    let total = if o.findings.total > 0 {
        o.findings.total as u64
    } else {
        findings.len() as u64
    };
    let mut col = column![text(format!(
        "{}{} findings",
        findings.len(),
        if total as usize > findings.len() {
            format!(" of {total}")
        } else {
            String::new()
        }
    ))
    .size(typo::META)
    .color(hud.tokens().muted)]
    .spacing(10);
    for f in findings {
        let sev = f.severity.clone();
        let title = if f.title.is_empty() {
            "Finding".into()
        } else {
            f.title.clone()
        };
        let detail = f.detail.clone();
        let primary = f
            .primary_event_index
            .or_else(|| f.event_indices.first().copied());
        let mut card = column![
            text(format!("{} {} {}", sev, f.plugin_id, f.category))
                .size(typo::META)
                .color(severity_color(&sev, hud.tokens())),
            button(
                text(title)
                    .size(typo::TITLE)
                    .font(typo::UI_BOLD)
                    .color(hud.tokens().text),
            )
            .on_press(
                primary
                    .map(Message::JumpTimeline)
                    .unwrap_or(Message::SetTab(Tab::Timeline))
            )
            .style(style::quiet(hud.tokens()))
            .padding([4, 8]),
        ]
        .spacing(4);
        if !detail.is_empty() {
            card = card.push(md_body(&detail, 2500, typo::BODY, md_style(hud)));
        }
        col = col.push(
            container(card)
                .padding(16)
                .style(move |_| style::card(hud.tokens(), false)),
        );
    }
    col.into()
}

fn notes_tab(hud: &Hud) -> Element<'_, Message> {
    let o = hud.overview().unwrap();
    let mut notes: Vec<&NoteRow> = o.notes.notes.iter().collect();
    notes.sort_by(|a, b| b.updated_at.cmp(&a.updated_at));
    let specs = hud.notes_schema();
    let editing = !hud.note_draft().id.is_empty();
    let mut form = column![text(if editing { "Edit note" } else { "Add note" })
        .size(typo::TITLE)
        .font(typo::UI_BOLD)
        .color(hud.tokens().text)]
    .spacing(8);
    for spec in specs {
        let id = spec.id;
        let label = spec.label;
        let val = hud.note_draft().field(&id);
        form = form.push(
            text(label.clone())
                .size(typo::META)
                .color(hud.tokens().muted),
        );
        form = form.push(
            text_input(label.as_str(), val)
                .font(typo::UI)
                .size(typo::BODY)
                .on_input(move |v| Message::NoteField {
                    id: id.clone(),
                    value: v,
                })
                .padding(8)
                .style(style::search(hud.tokens())),
        );
    }
    form = form.push(text("Turn").size(typo::META).color(hud.tokens().muted));
    form = form.push(
        text_input("session", &hud.note_draft().turn_index)
            .font(typo::UI)
            .size(typo::BODY)
            .on_input(Message::NoteTurn)
            .padding(8)
            .style(style::search(hud.tokens()))
            .width(Length::Fixed(120.0)),
    );
    if !hud.note_draft().event_index.is_empty() {
        form = form.push(
            text(format!("Event #{}", hud.note_draft().event_index))
                .size(typo::META)
                .color(hud.tokens().muted),
        );
    }
    let mut actions = row![button(if hud.note_saving() {
        "Saving…"
    } else if editing {
        "Save"
    } else {
        "Save note"
    })
    .on_press(Message::SaveNote)
    .style(style::quiet(hud.tokens()))
    .padding([8, 12])]
    .spacing(8);
    if editing {
        let nid = hud.note_draft().id.clone();
        let del = if hud.note_delete_armed() == nid {
            "Delete?"
        } else {
            "Delete"
        };
        actions = actions.push(
            button(del)
                .on_press(Message::RequestDelete(nid))
                .style(style::danger(hud.tokens()))
                .padding([8, 12]),
        );
        actions = actions.push(
            button("New note")
                .on_press(Message::ResetDraft)
                .style(style::quiet(hud.tokens()))
                .padding([8, 12]),
        );
    }
    form = form.push(actions);

    let rev = o.notes.revision.clone();
    let mut col = column![
        form,
        text(format!(
            "{} note{}{}",
            notes.len(),
            if notes.len() == 1 { "" } else { "s" },
            if rev.is_empty() {
                String::new()
            } else {
                format!(" · rev {}", rev.chars().take(12).collect::<String>())
            }
        ))
        .size(typo::META)
        .color(hud.tokens().muted)
    ]
    .spacing(12);
    if notes.is_empty() {
        col = col.push(
            text("No notes yet.")
                .size(typo::BODY)
                .color(hud.tokens().muted),
        );
    } else {
        for n in notes {
            let id = n.id.clone();
            let (title, body, extras) = note_fields_view(&n.fields);
            let turn = n.turn_index.map(|i| i.to_string()).unwrap_or_default();
            let when = {
                if n.updated_at.is_empty() {
                    format_note_time(&n.created_at)
                } else {
                    format_note_time(&n.updated_at)
                }
            };
            let mut card = column![
                text(format!(
                    "{} · {}",
                    if turn.is_empty() || turn == "null" {
                        "Session".into()
                    } else {
                        format!("Turn {turn}")
                    },
                    when
                ))
                .size(typo::META)
                .color(hud.tokens().muted),
                text(if title.is_empty() {
                    "Empty note".into()
                } else {
                    title
                })
                .size(typo::TITLE)
                .font(typo::UI_BOLD)
                .color(hud.tokens().text),
            ]
            .spacing(4);
            if !body.is_empty() {
                card = card.push(md_body(&body, 4000, typo::BODY, md_style(hud)));
            }
            for (k, v) in extras.into_iter().take(8) {
                card = card.push(
                    text(format!("{k}: {v}"))
                        .size(typo::META)
                        .color(hud.tokens().muted),
                );
            }
            let del_label = if hud.note_delete_armed() == id {
                "Delete?"
            } else {
                "Delete"
            };
            card = card.push(
                row![
                    button("Edit")
                        .on_press(Message::OpenNote(id.clone()))
                        .style(style::quiet(hud.tokens()))
                        .padding([6, 10]),
                    button(del_label)
                        .on_press(Message::RequestDelete(id))
                        .style(style::danger(hud.tokens()))
                        .padding([6, 10]),
                ]
                .spacing(6),
            );
            col = col.push(
                container(card)
                    .padding(16)
                    .style(move |_| style::card(hud.tokens(), false)),
            );
        }
    }
    col.into()
}

fn tone_color(tone: &str, tok: crate::theme::Tokens) -> Color {
    match tone {
        "awaiting" => tok.warning,
        "running" => tok.success,
        "complete" => tok.primary,
        "ending" => tok.accent,
        "cancelled" => tok.error,
        _ => tok.muted,
    }
}

fn event_payload(ev: &TimelineEvent, selected: bool, hud: &Hud) -> Element<'static, Message> {
    let kind = ev.kind.clone();
    let tool = ev.tool_name.clone();
    let preview = ev.preview.clone();
    let content = ev.content.clone();
    let raw_body = timeline_body_text(&preview, &content, selected, 240);
    let body = sanitize_console_text(&raw_body);
    let tok = hud.tokens();
    if !selected {
        return render_payload_text(&body, &kind, hud, false);
    }
    let mut col = column![].spacing(8);
    let family = ev.tool_family.clone();
    let call_id = ev.tool_call_id.clone();
    if !tool.is_empty() || !family.is_empty() || !call_id.is_empty() {
        let mut bits = vec![];
        if !tool.is_empty() {
            bits.push(tool.clone());
        }
        if !family.is_empty() {
            bits.push(family);
        }
        if !call_id.is_empty() {
            bits.push(call_id);
        }
        col = col.push(
            text(bits.join(" · "))
                .size(typo::META)
                .color(tok.muted)
                .font(typo::MONO),
        );
    }
    if kind == "tool" || kind == "tool_result" {
        let raw = &ev.raw_input;
        if !raw.is_null() && raw != &json!({}) {
            col = col.push(text("Input").size(typo::META).color(tok.muted));
            col = col.push(code_block(&capped_json(raw, 2_000), hud));
        }
        if !body.trim().is_empty() {
            col = col.push(text("Output").size(typo::META).color(tok.muted));
            col = col.push(render_payload_text(&body, &kind, hud, true));
        }
    } else {
        col = col.push(render_payload_text(&body, &kind, hud, true));
    }
    col.into()
}

fn render_payload_text(
    body: &str,
    kind: &str,
    hud: &Hud,
    expanded: bool,
) -> Element<'static, Message> {
    let tok = hud.tokens();
    let trimmed = body.trim();
    if trimmed.is_empty() {
        return text("empty").size(typo::META).color(tok.muted).into();
    }
    let max = if expanded { 4_000 } else { 400 };
    let cut = capped_display(body, max);
    if !expanded {
        return text(cut)
            .size(typo::BODY)
            .font(typo::UI)
            .color(tok.muted)
            .into();
    }
    // Plain text only: iced markdown on a multi‑k body freezes or aborts
    // the palette on large host sessions.
    let rendered: Element<'static, Message> = match kind {
        "thought" => text(cut)
            .size(typo::BODY)
            .font(typo::UI_ITALIC)
            .color(tok.muted)
            .into(),
        "plan" => text(cut).size(typo::BODY).color(tok.accent).into(),
        "session" | "task" => text(cut).size(typo::BODY).color(tok.warning).into(),
        "error" => text(cut).size(typo::BODY).color(tok.error).into(),
        "system" => text(cut).size(typo::BODY).color(tok.accent).into(),
        _ if looks_like_json(trimmed) => return code_block(&cut, hud),
        _ => text(cut).size(typo::BODY).font(typo::UI).into(),
    };
    if expanded
        && (kind == "user" || kind == "agent" || kind == "subagent" || looks_like_markdown(body))
    {
        inset_body(rendered, hud)
    } else {
        rendered
    }
}

fn code_block(src: &str, hud: &Hud) -> Element<'static, Message> {
    let pretty = if looks_like_json(src) {
        pretty_json(src)
    } else {
        src.to_string()
    };
    let pretty = capped_display(&pretty, 2_000);
    let tok = hud.tokens();
    container(
        text(pretty)
            .size(typo::META)
            .font(typo::MONO)
            .color(tok.text),
    )
    .padding(8)
    .width(Length::Fill)
    .style(move |_| style::inset(tok))
    .into()
}

fn inset_body(inner: Element<'static, Message>, hud: &Hud) -> Element<'static, Message> {
    let tok = hud.tokens();
    container(inner)
        .padding(10)
        .width(Length::Fill)
        .style(move |_| style::inset(tok))
        .into()
}

fn role_color(role: EventRole, tok: crate::theme::Tokens) -> Color {
    match role {
        EventRole::User => tok.text,
        EventRole::Model => tok.primary,
        EventRole::ModelDim => crate::theme::mix(tok.primary, tok.canvas, 0.55),
        EventRole::Session => tok.warning,
        EventRole::Error => tok.error,
        EventRole::System => tok.accent,
        EventRole::Other => tok.muted,
    }
}

fn severity_color(sev: &str, tok: crate::theme::Tokens) -> Color {
    match sev.to_ascii_lowercase().as_str() {
        "high" | "error" | "critical" => tok.error,
        "medium" | "warn" | "warning" => tok.warning,
        "low" | "info" => tok.accent,
        _ => tok.muted,
    }
}

const POP_OUT_PX: f32 = 16.0;

fn pop_out_control(tok: crate::theme::Tokens) -> Element<'static, Message> {
    mouse_area(
        container(
            Canvas::new(PopOutIcon { color: tok.muted })
                .width(Length::Fixed(POP_OUT_PX))
                .height(Length::Fixed(POP_OUT_PX)),
        )
        .padding([6, 8]),
    )
    .on_press(Message::PopOutWindow)
    .into()
}

#[derive(Debug, Clone, Copy)]
struct PopOutIcon {
    color: Color,
}

/// Box in the lower-left, arrow leaving toward the upper-right.
fn pop_out_marks(size: f32) -> (Point, Size, Point, Point, Point, Point) {
    let pad = size * 0.16;
    let box_s = size * 0.52;
    let box_tl = Point::new(pad, size - pad - box_s);
    let tail = Point::new(size * 0.46, size * 0.54);
    let tip = Point::new(size - pad, pad);
    let arm = size * 0.26;
    (
        box_tl,
        Size::new(box_s, box_s),
        tail,
        tip,
        Point::new(tip.x - arm, tip.y),
        Point::new(tip.x, tip.y + arm),
    )
}

impl canvas::Program<Message> for PopOutIcon {
    type State = ();

    fn draw(
        &self,
        _state: &Self::State,
        renderer: &Renderer,
        _theme: &Theme,
        bounds: Rectangle,
        _cursor: mouse::Cursor,
    ) -> Vec<canvas::Geometry> {
        let mut frame = canvas::Frame::new(renderer, bounds.size());
        let stroke = canvas::Stroke::default()
            .with_color(self.color)
            .with_width(1.6)
            .with_line_cap(canvas::LineCap::Round)
            .with_line_join(canvas::LineJoin::Round);
        let size = bounds.width.min(bounds.height);
        let (box_tl, box_sz, tail, tip, left, down) = pop_out_marks(size);
        frame.stroke_rectangle(box_tl, box_sz, stroke);
        let arrow = canvas::Path::new(|b| {
            b.move_to(tail);
            b.line_to(tip);
            b.move_to(left);
            b.line_to(tip);
            b.line_to(down);
        });
        frame.stroke(&arrow, stroke);
        vec![frame.into_geometry()]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pop_out_marks_stay_inside_icon() {
        let size = 16.0;
        let (box_tl, box_sz, tail, tip, left, down) = pop_out_marks(size);
        for p in [box_tl, tail, tip, left, down] {
            assert!(p.x >= 0.0 && p.x <= size, "{p:?}");
            assert!(p.y >= 0.0 && p.y <= size, "{p:?}");
        }
        assert!(box_tl.x + box_sz.width <= size);
        assert!(box_tl.y + box_sz.height <= size);
        assert!(tip.x > tail.x && tip.y < tail.y);
    }
}

//! Keyboard shortcut tables for the HUD footer and help cheatsheet.
//!
//! icedtea [`pattern::status_bar`] prints [`ActionTable::footer_hints`];
//! [`pattern::cheatsheet`] lists the full table. One table shape, two views.

use icedtea::action::{Action, ActionTable};
use icedtea::shortcut::Shortcut;

use crate::app::Message;
use crate::model::Tab;

/// What the footer should advertise right now.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct KeyScope {
    pub browse: bool,
    pub help_open: bool,
    pub timeline_detail: bool,
    pub tab: Tab,
}

fn push(table: &mut ActionTable<Message>, id: &str, title: &str, spec: &str, msg: Message) {
    table.insert(
        Action::new(id, title, msg)
            .with_shortcut(Shortcut::parse(spec).expect("static HUD shortcut spec")),
    );
}

/// Primary keys for the status-bar footer (short, context-filtered).
pub fn footer_table(scope: KeyScope) -> ActionTable<Message> {
    let mut table = ActionTable::new();
    if scope.help_open {
        push(&mut table, "help.toggle", "Help", "?", Message::ToggleHelp);
        push(&mut table, "overlay.hide", "Close", "escape", Message::Hide);
        return table;
    }
    push(&mut table, "help.toggle", "Help", "?", Message::ToggleHelp);
    let hide = if scope.timeline_detail {
        "Timeline"
    } else {
        "Hide"
    };
    push(&mut table, "overlay.hide", hide, "escape", Message::Hide);
    if !scope.browse {
        push(
            &mut table,
            "session.open",
            "Open",
            "enter",
            Message::ActivateSelected,
        );
        push(&mut table, "list.down", "Down", "j", Message::Noop);
        push(&mut table, "search.focus", "Search", "/", Message::Noop);
        return table;
    }
    push(&mut table, "search.focus", "Search", "/", Message::Noop);
    push(&mut table, "pane.next", "Panes", "tab", Message::Noop);
    if scope.timeline_detail {
        push(&mut table, "list.down", "Step", "j", Message::Noop);
    } else if matches!(scope.tab, Tab::Turns | Tab::Timeline) {
        push(&mut table, "list.down", "Down", "j", Message::Noop);
    }
    push(
        &mut table,
        "session.open",
        "Next",
        "enter",
        Message::ActivateSelected,
    );
    push(&mut table, "edit.copy", "Copy", "y", Message::Yank);
    table
}

/// Full shortcut list for the `?` cheatsheet.
pub fn help_table() -> ActionTable<Message> {
    let mut table = ActionTable::new();
    push(&mut table, "help.toggle", "Help", "?", Message::ToggleHelp);
    push(
        &mut table,
        "overlay.hide",
        "Hide overlay",
        "escape",
        Message::Hide,
    );
    push(
        &mut table,
        "session.open",
        "Open or next",
        "enter",
        Message::ActivateSelected,
    );
    push(&mut table, "list.down", "Move down", "j", Message::Noop);
    push(&mut table, "list.up", "Move up", "k", Message::Noop);
    push(&mut table, "pane.next", "Next pane", "tab", Message::Noop);
    push(
        &mut table,
        "pane.prev",
        "Previous pane",
        "shift+tab",
        Message::Noop,
    );
    for (i, tab) in Tab::ALL.iter().enumerate() {
        let n = i + 1;
        push(
            &mut table,
            &format!("pane.{n}"),
            tab.label(),
            &format!("ctrl+{n}"),
            Message::SetTab(*tab),
        );
    }
    push(&mut table, "edit.copy", "Copy", "y", Message::Yank);
    push(
        &mut table,
        "edit.copy_chord",
        "Copy",
        "ctrl+shift+c",
        Message::Yank,
    );
    push(&mut table, "search.focus", "Search", "/", Message::Noop);
    push(
        &mut table,
        "events.next_turn",
        "Next turn",
        "]",
        Message::Noop,
    );
    push(
        &mut table,
        "events.all_turns",
        "All turns",
        "[",
        Message::Noop,
    );
    push(
        &mut table,
        "turns.timeline",
        "Timeline for turn",
        "g",
        Message::Noop,
    );
    table
}

#[cfg(test)]
mod tests {
    use super::*;

    fn picker() -> KeyScope {
        KeyScope {
            browse: false,
            help_open: false,
            timeline_detail: false,
            tab: Tab::Overview,
        }
    }

    #[test]
    fn help_table_lists_unique_shortcuts() {
        let table = help_table();
        assert!(table.conflicts().is_empty());
        assert!(table.get("help.toggle").is_some());
        assert!(table.get("overlay.hide").is_some());
        assert!(table.get("list.down").is_some());
        assert!(table.get("list.up").is_some());
        assert!(table.get("pane.1").is_some());
        assert!(table.get("pane.5").is_some());
        assert!(table.get("edit.copy").is_some());
        assert!(table.get("search.focus").is_some());
        let hints = table.footer_hints();
        assert!(hints.iter().any(|h| h.starts_with("? ")));
        assert!(hints.iter().any(|h| h.contains("esc")));
    }

    #[test]
    fn footer_table_picker_is_short() {
        let hints = footer_table(picker()).footer_hints();
        let blob = hints.join("  ·  ");
        assert!(blob.contains("? help"));
        assert!(blob.contains("esc hide"));
        assert!(blob.contains("enter open"));
        assert!(blob.contains("j down"));
        assert!(blob.contains("/ search"));
        assert!(!blob.contains("tab "));
        assert!(!blob.contains("y copy"));
    }

    #[test]
    fn footer_table_browse_and_timeline_detail() {
        let browse = footer_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            tab: Tab::Overview,
        });
        let blob = browse.footer_hints().join("  ·  ");
        assert!(blob.contains("tab panes"));
        assert!(blob.contains("y copy"));
        assert!(!blob.contains("j "));

        let turns = footer_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            tab: Tab::Turns,
        });
        assert!(turns.footer_hints().iter().any(|h| h.contains("j down")));

        let detail = footer_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: true,
            tab: Tab::Timeline,
        });
        let blob = detail.footer_hints().join("  ·  ");
        assert!(blob.contains("esc timeline"));
        assert!(blob.contains("j step"));
    }

    #[test]
    fn footer_table_help_open_is_close_only() {
        let hints = footer_table(KeyScope {
            browse: true,
            help_open: true,
            timeline_detail: true,
            tab: Tab::Timeline,
        })
        .footer_hints();
        let blob = hints.join("  ·  ");
        assert!(blob.contains("? help"));
        assert!(blob.contains("esc close"));
        assert_eq!(hints.len(), 2);
    }
}

//! Shared HUD value types.

pub use crate::wire::SessionListItem as SessionRow;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tab {
    Overview,
    Turns,
    Timeline,
    Findings,
    Notes,
}

impl Tab {
    pub const ALL: [Tab; 5] = [
        Tab::Overview,
        Tab::Turns,
        Tab::Timeline,
        Tab::Findings,
        Tab::Notes,
    ];

    pub fn label(self) -> &'static str {
        match self {
            Tab::Overview => "Overview",
            Tab::Turns => "Turns",
            Tab::Timeline => "Timeline",
            Tab::Findings => "Findings",
            Tab::Notes => "Notes",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum KindFilter {
    #[default]
    All,
    Tools,
    User,
    Asst,
    Sess,
    Errors,
}

impl KindFilter {
    pub const ALL: [KindFilter; 6] = [
        KindFilter::All,
        KindFilter::Tools,
        KindFilter::User,
        KindFilter::Asst,
        KindFilter::Sess,
        KindFilter::Errors,
    ];

    pub fn label(self) -> &'static str {
        match self {
            KindFilter::All => "All events",
            KindFilter::Tools => "Tools only",
            KindFilter::User => "User messages",
            KindFilter::Asst => "Assistant",
            KindFilter::Sess => "Session markers",
            KindFilter::Errors => "Errors only",
        }
    }
}

impl std::fmt::Display for KindFilter {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.label())
    }
}

#[derive(Debug, Clone, Default)]
pub struct NoteDraft {
    pub id: String,
    pub turn_index: String,
    pub event_index: String,
    pub fields: Vec<(String, String)>,
}

impl NoteDraft {
    pub fn field(&self, id: &str) -> &str {
        self.fields
            .iter()
            .find(|(k, _)| k == id)
            .map(|(_, v)| v.as_str())
            .unwrap_or("")
    }

    pub fn set_field(&mut self, id: &str, value: String) {
        if let Some(slot) = self.fields.iter_mut().find(|(k, _)| k == id) {
            slot.1 = value;
        } else {
            self.fields.push((id.to_string(), value));
        }
    }

    pub fn has_content(&self) -> bool {
        self.fields.iter().any(|(_, v)| !v.trim().is_empty())
    }
}

#[derive(Debug, Clone)]
pub struct SchemaField {
    pub id: String,
    pub label: String,
    #[allow(dead_code)]
    pub choices: Vec<String>,
    #[allow(dead_code)]
    pub pick: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tab_and_kind_labels() {
        assert_eq!(Tab::Overview.label(), "Overview");
        assert_eq!(Tab::Turns.label(), "Turns");
        assert_eq!(Tab::Timeline.label(), "Timeline");
        assert_eq!(Tab::Findings.label(), "Findings");
        assert_eq!(Tab::Notes.label(), "Notes");
        assert_eq!(Tab::ALL.len(), 5);
        assert_eq!(KindFilter::All.label(), "All events");
        assert_eq!(KindFilter::Asst.to_string(), "Assistant");
        assert_eq!(KindFilter::Sess.label(), "Session markers");
        let mut draft = NoteDraft::default();
        assert!(!draft.has_content());
        draft.set_field("summary", "hi".into());
        assert_eq!(draft.field("summary"), "hi");
        assert!(draft.has_content());
        draft.set_field("summary", "yo".into());
        assert_eq!(draft.field("summary"), "yo");
        assert_eq!(draft.field("missing"), "");
    }
}

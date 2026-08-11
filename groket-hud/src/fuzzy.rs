//! fzf-style fuzzy match (ported from groket.ui.fuzzy).

const SCORE_MATCH: i32 = 16;
const BONUS_BOUNDARY: i32 = 8;
const BONUS_BOUNDARY_WHITE: i32 = 10;
const BONUS_CAMEL: i32 = 10;
const BONUS_CONSECUTIVE: i32 = 4;
const BONUS_FIRST_MULT: i32 = 2;
const PENALTY_GAP_START: i32 = -3;
const PENALTY_GAP_EXTEND: i32 = -1;

fn is_boundary(c: char) -> bool {
    matches!(
        c,
        '/' | '-' | '_' | '.' | ' ' | ',' | ';' | ':' | '\\' | '\t'
    )
}

fn char_bonus(prev: Option<char>, curr: char) -> i32 {
    let Some(p) = prev else {
        return BONUS_BOUNDARY_WHITE;
    };
    if is_boundary(p) {
        return if p == ' ' || p == '\t' {
            BONUS_BOUNDARY_WHITE
        } else {
            BONUS_BOUNDARY
        };
    }
    if p.is_lowercase() && curr.is_uppercase() {
        return BONUS_CAMEL;
    }
    0
}

pub fn fzf_score(query: &str, candidate: &str) -> i32 {
    let ql: Vec<char> = query.to_lowercase().chars().collect();
    let cl: Vec<char> = candidate.to_lowercase().chars().collect();
    let orig: Vec<char> = candidate.chars().collect();
    let qchars: Vec<char> = query.chars().collect();
    if ql.is_empty() {
        return 0;
    }
    if ql.len() > cl.len() {
        return 0;
    }
    let mut positions = Vec::new();
    let mut j = 0;
    for (i, c) in cl.iter().enumerate() {
        if j < ql.len() && *c == ql[j] {
            positions.push(i);
            j += 1;
        }
    }
    if j < ql.len() {
        return 0;
    }
    let mut score = 0;
    let mut consecutive = 0;
    for (k, &pos) in positions.iter().enumerate() {
        let prev = if pos > 0 {
            orig.get(pos - 1).copied()
        } else {
            None
        };
        let curr = orig.get(pos).copied().unwrap_or(' ');
        let bonus = char_bonus(prev, curr);
        let mut char_score = SCORE_MATCH + bonus;
        if k == 0 && bonus > 0 {
            char_score += bonus * (BONUS_FIRST_MULT - 1);
        }
        if consecutive > 0 {
            char_score += BONUS_CONSECUTIVE;
        } else if k > 0 {
            let gap = pos - (positions[k - 1] + 1);
            if gap > 0 {
                char_score += PENALTY_GAP_START + PENALTY_GAP_EXTEND * (gap as i32 - 1);
            }
        }
        if qchars.get(k).copied() == orig.get(pos).copied() {
            char_score += 1;
        }
        score += char_score.max(0);
        if k > 0 && pos == positions[k - 1] + 1 {
            consecutive += 1;
        } else {
            consecutive = 0;
        }
    }
    score
}

pub fn fuzzy_filter<T, F>(query: &str, items: &[T], mut text_fn: F) -> Vec<T>
where
    T: Clone,
    F: FnMut(&T) -> String,
{
    let q = query.trim();
    if q.is_empty() {
        return items.to_vec();
    }
    let mut scored: Vec<(i32, T)> = Vec::new();
    for item in items {
        let text = text_fn(item);
        let score = fzf_score(q, &text);
        if score > 0 {
            scored.push((score, item.clone()));
        }
    }
    scored.sort_by_key(|b| std::cmp::Reverse(b.0));
    scored.into_iter().map(|(_, t)| t).collect()
}

/// Same rank as [`fuzzy_filter`], but returns source indices and does not clone *items*.
pub fn fuzzy_filter_indices<T, F>(query: &str, items: &[T], mut text_fn: F) -> Vec<usize>
where
    F: FnMut(&T) -> String,
{
    let q = query.trim();
    if q.is_empty() {
        return (0..items.len()).collect();
    }
    let mut scored: Vec<(i32, usize)> = Vec::new();
    for (i, item) in items.iter().enumerate() {
        let text = text_fn(item);
        let score = fzf_score(q, &text);
        if score > 0 {
            scored.push((score, i));
        }
    }
    scored.sort_by_key(|b| std::cmp::Reverse(b.0));
    scored.into_iter().map(|(_, i)| i).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matches_subsequence() {
        assert!(fzf_score("hud", "groket-hud palette") > 0);
        assert_eq!(fzf_score("zzz", "groket"), 0);
    }

    #[test]
    fn filter_keeps_order_by_score() {
        let items = ["zzz", "hud window", "session"];
        let out = fuzzy_filter("hud", &items, |s| (*s).to_string());
        assert_eq!(out, vec!["hud window"]);
    }

    #[test]
    fn filter_indices_match_owned_filter() {
        let items = ["zzz", "hud window", "session"];
        let owned = fuzzy_filter("hud", &items, |s| (*s).to_string());
        let idxs = fuzzy_filter_indices("hud", &items, |s| (*s).to_string());
        let via_idx: Vec<&str> = idxs.iter().map(|&i| items[i]).collect();
        assert_eq!(via_idx, owned);
        assert_eq!(
            fuzzy_filter_indices("", &items, |s| (*s).to_string()),
            vec![0, 1, 2]
        );
    }
}

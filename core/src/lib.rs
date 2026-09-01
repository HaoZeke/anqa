//! Typed session ingest. Every harness store implements [`store::Store`].

pub mod event;
pub mod jsonl;
pub mod scan;
pub mod store;
pub mod stores;
pub mod text;
pub mod walk;

use event::{Event, FileStamp, ListMeta, SessionLocator};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{LazyLock, Mutex};

type TimelineCache = HashMap<String, (FileStamp, Vec<Event>)>;

static TIMELINE: LazyLock<Mutex<TimelineCache>> = LazyLock::new(|| Mutex::new(HashMap::new()));

fn cache_key(harness: &str, locator: &Path, session_id: &str) -> String {
    format!("{harness}\0{}\0{session_id}", locator.display())
}

pub fn discover(harness: &str, roots: &[PathBuf]) -> Result<Vec<SessionLocator>, String> {
    let store = store::by_id(harness).ok_or_else(|| format!("unknown harness: {harness}"))?;
    Ok(store.discover(roots))
}

pub fn list_meta(harness: &str, locator: &Path, session_id: &str) -> Result<ListMeta, String> {
    let store = store::by_id(harness).ok_or_else(|| format!("unknown harness: {harness}"))?;
    store.list_meta(locator, session_id)
}

fn cached_timeline(
    harness: &str,
    locator: &Path,
    session_id: &str,
) -> Result<(FileStamp, Vec<Event>), String> {
    let store = store::by_id(harness).ok_or_else(|| format!("unknown harness: {harness}"))?;
    let stamp = store.stamp(locator);
    let key = cache_key(harness, locator, session_id);
    if let Ok(guard) = TIMELINE.lock() {
        if let Some((got, evs)) = guard.get(&key) {
            if *got == stamp {
                return Ok((stamp, evs.clone()));
            }
        }
    }
    let mut evs = store.timeline(locator, session_id)?;
    Event::carry_turn_numbers(&mut evs);
    if let Ok(mut guard) = TIMELINE.lock() {
        guard.insert(key, (stamp, evs.clone()));
    }
    Ok((stamp, evs))
}

fn page_slice(events: &[Event], offset: usize, limit: usize) -> Vec<Event> {
    if offset >= events.len() || limit == 0 {
        return Vec::new();
    }
    let end = (offset + limit).min(events.len());
    events[offset..end].to_vec()
}

pub fn timeline(harness: &str, locator: &Path, session_id: &str) -> Result<Vec<Event>, String> {
    Ok(cached_timeline(harness, locator, session_id)?.1)
}

pub fn timeline_page(
    harness: &str,
    locator: &Path,
    session_id: &str,
    offset: usize,
    limit: usize,
) -> Result<(Vec<Event>, usize), String> {
    let store = store::by_id(harness).ok_or_else(|| format!("unknown harness: {harness}"))?;
    let stamp = store.stamp(locator);
    let key = cache_key(harness, locator, session_id);
    if let Ok(guard) = TIMELINE.lock() {
        if let Some((got, evs)) = guard.get(&key) {
            if *got == stamp {
                return Ok((page_slice(evs, offset, limit), evs.len()));
            }
        }
    }
    let mut evs = store.timeline(locator, session_id)?;
    Event::carry_turn_numbers(&mut evs);
    let page = page_slice(&evs, offset, limit);
    let total = evs.len();
    if let Ok(mut guard) = TIMELINE.lock() {
        guard.insert(key, (stamp, evs));
    }
    Ok((page, total))
}

pub fn stamp(harness: &str, locator: &Path) -> Result<FileStamp, String> {
    let store = store::by_id(harness).ok_or_else(|| format!("unknown harness: {harness}"))?;
    Ok(store.stamp(locator))
}

#[cfg(feature = "extension-module")]
mod pybind {
    use super::*;
    use pyo3::prelude::*;
    use pyo3::types::{PyDict, PyList};

    fn event_dict<'py>(py: Python<'py>, ev: &Event) -> PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        d.set_item("index", ev.index)?;
        d.set_item("event_type", ev.event_type.as_str())?;
        d.set_item("timestamp", ev.timestamp)?;
        d.set_item("content", ev.content.as_str())?;
        d.set_item("raw", ev.raw.as_str())?;
        d.set_item("tool_name", ev.tool_name.as_str())?;
        d.set_item("tool_call_id", ev.tool_call_id.as_str())?;
        d.set_item("is_error", ev.is_error)?;
        d.set_item("update_index", ev.update_index)?;
        d.set_item("prompt_index", ev.prompt_index)?;
        d.set_item("turn_number", ev.turn_number)?;
        d.set_item("child_session_id", ev.child_session_id.as_str())?;
        d.set_item("subagent_type", ev.subagent_type.as_str())?;
        d.set_item("description", ev.description.as_str())?;
        Ok(d)
    }

    #[pyfunction]
    fn keep_updates_line(line: &[u8]) -> bool {
        crate::scan::keep_updates_line(line)
    }

    #[pyfunction]
    fn filter_updates(data: &[u8]) -> Vec<Vec<u8>> {
        crate::scan::filter_updates(data)
    }

    #[pyfunction]
    fn find_sessions(root: &str) -> Vec<String> {
        crate::walk::find_sessions(Path::new(root))
            .into_iter()
            .map(|p| p.to_string_lossy().into_owned())
            .collect()
    }

    #[pyfunction]
    fn looks_like_session_dir(path: &str) -> bool {
        crate::walk::looks_like_session_dir(Path::new(path))
    }

    #[pyfunction]
    fn find_files(root: &str, suffix: &str, name_prefix: &str) -> Vec<String> {
        crate::walk::find_files(Path::new(root), suffix, name_prefix)
            .into_iter()
            .map(|p| p.to_string_lossy().into_owned())
            .collect()
    }

    #[pyfunction]
    fn store_ids() -> Vec<&'static str> {
        crate::store::ids().to_vec()
    }

    #[pyfunction]
    fn store_discover<'py>(
        py: Python<'py>,
        harness: &str,
        roots: Vec<String>,
    ) -> PyResult<Bound<'py, PyList>> {
        let paths: Vec<PathBuf> = roots.into_iter().map(PathBuf::from).collect();
        let found = super::discover(harness, &paths)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;
        let list = PyList::empty(py);
        for loc in found {
            let d = PyDict::new(py);
            d.set_item("harness", loc.harness)?;
            d.set_item("session_id", loc.session_id)?;
            d.set_item("locator", loc.locator.to_string_lossy().as_ref())?;
            d.set_item("cwd", loc.cwd)?;
            list.append(d)?;
        }
        Ok(list)
    }

    #[pyfunction]
    fn store_list_meta<'py>(
        py: Python<'py>,
        harness: &str,
        locator: &str,
        session_id: &str,
    ) -> PyResult<Bound<'py, PyDict>> {
        let meta = super::list_meta(harness, Path::new(locator), session_id)
            .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
        let d = PyDict::new(py);
        d.set_item("session_id", meta.session_id)?;
        d.set_item("locator", meta.locator.to_string_lossy().as_ref())?;
        d.set_item("model_id", meta.model_id)?;
        d.set_item("title", meta.title)?;
        d.set_item("created_at", meta.created_at)?;
        d.set_item("updated_at", meta.updated_at)?;
        d.set_item("duration_seconds", meta.duration_seconds)?;
        d.set_item("tool_call_count", meta.tool_call_count)?;
        d.set_item("turn_outcome", meta.turn_outcome)?;
        d.set_item("harness", meta.harness)?;
        d.set_item("harness_version", meta.harness_version)?;
        d.set_item("run_dir", meta.run_dir)?;
        d.set_item("num_events", meta.num_events)?;
        d.set_item("has_subagents", meta.has_subagents)?;
        d.set_item("subagent_count", meta.subagent_count)?;
        Ok(d)
    }

    #[pyfunction]
    fn store_timeline<'py>(
        py: Python<'py>,
        harness: &str,
        locator: &str,
        session_id: &str,
    ) -> PyResult<Bound<'py, PyList>> {
        let evs = super::timeline(harness, Path::new(locator), session_id)
            .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
        let list = PyList::empty(py);
        for ev in evs {
            list.append(event_dict(py, &ev)?)?;
        }
        Ok(list)
    }

    #[pyfunction]
    fn store_timeline_page<'py>(
        py: Python<'py>,
        harness: &str,
        locator: &str,
        session_id: &str,
        offset: usize,
        limit: usize,
    ) -> PyResult<Bound<'py, PyDict>> {
        let (page, total) =
            super::timeline_page(harness, Path::new(locator), session_id, offset, limit)
                .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
        let events = PyList::empty(py);
        for ev in page {
            events.append(event_dict(py, &ev)?)?;
        }
        let d = PyDict::new(py);
        d.set_item("events", events)?;
        d.set_item("total", total)?;
        Ok(d)
    }

    #[pyfunction]
    fn store_stamp(harness: &str, locator: &str) -> PyResult<(f64, u64, u64, u64)> {
        super::stamp(harness, Path::new(locator)).map_err(pyo3::exceptions::PyRuntimeError::new_err)
    }

    #[pymodule]
    fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(keep_updates_line, m)?)?;
        m.add_function(wrap_pyfunction!(filter_updates, m)?)?;
        m.add_function(wrap_pyfunction!(find_sessions, m)?)?;
        m.add_function(wrap_pyfunction!(looks_like_session_dir, m)?)?;
        m.add_function(wrap_pyfunction!(find_files, m)?)?;
        m.add_function(wrap_pyfunction!(store_ids, m)?)?;
        m.add_function(wrap_pyfunction!(store_discover, m)?)?;
        m.add_function(wrap_pyfunction!(store_list_meta, m)?)?;
        m.add_function(wrap_pyfunction!(store_timeline, m)?)?;
        m.add_function(wrap_pyfunction!(store_timeline_page, m)?)?;
        m.add_function(wrap_pyfunction!(store_stamp, m)?)?;
        Ok(())
    }
}

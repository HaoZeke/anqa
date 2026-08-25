//! Session walk + ``updates.jsonl`` prefilter as ``anqa._scan``.

mod scan;
mod walk;

pub use scan::{filter_updates, keep_updates_line};
pub use walk::{find_sessions, looks_like_session_dir, skip_dir_name};

#[cfg(feature = "extension-module")]
mod pybind {
    use std::path::Path;

    use pyo3::prelude::*;

    use crate::scan;
    use crate::walk;

    #[pyfunction]
    fn keep_updates_line(line: &[u8]) -> bool {
        scan::keep_updates_line(line)
    }

    #[pyfunction]
    fn filter_updates(data: &[u8]) -> Vec<Vec<u8>> {
        scan::filter_updates(data)
    }

    #[pyfunction]
    fn find_sessions(root: &str) -> Vec<String> {
        walk::find_sessions(Path::new(root))
            .into_iter()
            .map(|p| p.to_string_lossy().into_owned())
            .collect()
    }

    #[pyfunction]
    fn looks_like_session_dir(path: &str) -> bool {
        walk::looks_like_session_dir(Path::new(path))
    }

    #[pymodule]
    fn _scan(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(keep_updates_line, m)?)?;
        m.add_function(wrap_pyfunction!(filter_updates, m)?)?;
        m.add_function(wrap_pyfunction!(find_sessions, m)?)?;
        m.add_function(wrap_pyfunction!(looks_like_session_dir, m)?)?;
        Ok(())
    }
}

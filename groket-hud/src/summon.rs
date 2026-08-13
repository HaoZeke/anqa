//! Unix summon socket for compositor binds (Sway/Wayland).
//!
//! In-process global-hotkey is X11-only on Linux. On Wayland the product path
//! is: keep a long-lived HUD, then ``show`` / ``hide`` / ``toggle`` over a
//! per-user runtime Unix socket (same layout as the control plane).
//!
//! Commands are one line each: ``show``, ``hide``, ``toggle`` (optional trailing
//! newline). Clients: ``groket-hud --show`` / ``groket hud --toggle``.

use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::sync::mpsc::{self, Receiver, RecvError, SyncSender};
use std::sync::{Mutex, OnceLock};
use std::thread;
use std::time::Duration;

use thiserror::Error;

/// Env override for the summon socket path.
pub const SOCKET_ENV: &str = "GROKET_HUD_SUMMON_SOCKET";

/// Operator action for the iced loop.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SummonAction {
    Show,
    Hide,
    Toggle,
}

#[derive(Debug, Error)]
pub enum SummonError {
    #[error("summon socket not available on this platform")]
    Unsupported,
    #[error("summon socket path could not be resolved")]
    NoPath,
    #[error("HUD summon socket not accepting ({0})")]
    NotRunning(String),
    #[error("{0}")]
    Io(#[from] std::io::Error),
    #[error("{0}")]
    Other(String),
}

/// Holds the listener thread and bound path for process lifetime.
pub struct SummonServer {
    path: PathBuf,
    #[allow(dead_code)]
    join: Option<thread::JoinHandle<()>>,
}

impl Drop for SummonServer {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
    }
}

/// Parse a single command line (trimmed, case-insensitive).
pub fn parse_command(raw: &str) -> Option<SummonAction> {
    match raw.trim().to_ascii_lowercase().as_str() {
        "show" => Some(SummonAction::Show),
        "hide" => Some(SummonAction::Hide),
        "toggle" => Some(SummonAction::Toggle),
        _ => None,
    }
}

/// Wire form for *action* (one word, no newline).
pub fn command_word(action: SummonAction) -> &'static str {
    match action {
        SummonAction::Show => "show",
        SummonAction::Hide => "hide",
        SummonAction::Toggle => "toggle",
    }
}

/// Default path: ``$XDG_RUNTIME_DIR/groket/hud-summon.sock``, or
/// ``~/.groket/run/hud-summon.sock`` when runtime dir is unset.
pub fn default_socket_path() -> Option<PathBuf> {
    if let Ok(raw) = std::env::var(SOCKET_ENV) {
        let t = raw.trim();
        if !t.is_empty() {
            return Some(PathBuf::from(t));
        }
    }
    if let Ok(runtime) = std::env::var("XDG_RUNTIME_DIR") {
        let t = runtime.trim();
        if !t.is_empty() {
            return Some(Path::new(t).join("groket").join("hud-summon.sock"));
        }
    }
    let home = std::env::var_os("HOME")?;
    Some(
        PathBuf::from(home)
            .join(".groket")
            .join("run")
            .join("hud-summon.sock"),
    )
}

/// True when a listener is bound (connect succeeds).
pub fn socket_accepts(path: &Path) -> bool {
    #[cfg(unix)]
    {
        std::os::unix::net::UnixStream::connect(path).is_ok()
    }
    #[cfg(not(unix))]
    {
        let _ = path;
        false
    }
}

/// Send one summon command to a running HUD.
pub fn send_command(action: SummonAction) -> Result<(), SummonError> {
    #[cfg(unix)]
    {
        let path = default_socket_path().ok_or(SummonError::NoPath)?;
        send_command_to(&path, action)
    }
    #[cfg(not(unix))]
    {
        let _ = action;
        Err(SummonError::Unsupported)
    }
}

/// Send *action* to *path*.
pub fn send_command_to(path: &Path, action: SummonAction) -> Result<(), SummonError> {
    #[cfg(unix)]
    {
        use std::os::unix::net::UnixStream;
        let mut stream = UnixStream::connect(path)
            .map_err(|err| SummonError::NotRunning(format!("{}: {err}", path.display())))?;
        let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
        let line = format!("{}\n", command_word(action));
        stream.write_all(line.as_bytes())?;
        stream.flush()?;
        Ok(())
    }
    #[cfg(not(unix))]
    {
        let _ = (path, action);
        Err(SummonError::Unsupported)
    }
}

/// Bind the summon socket and start the accept thread.
pub fn install() -> Result<SummonServer, SummonError> {
    #[cfg(unix)]
    {
        install_unix()
    }
    #[cfg(not(unix))]
    {
        Err(SummonError::Unsupported)
    }
}

/// Block until the next summon action (iced subscription).
pub fn recv_action() -> Result<SummonAction, RecvError> {
    loop {
        let outcome = {
            let guard = action_pair().1.lock().expect("summon action mutex");
            guard.try_recv()
        };
        match outcome {
            Ok(action) => return Ok(action),
            Err(std::sync::mpsc::TryRecvError::Disconnected) => return Err(RecvError),
            Err(std::sync::mpsc::TryRecvError::Empty) => {
                thread::sleep(Duration::from_millis(25));
            }
        }
    }
}

fn action_pair() -> &'static (SyncSender<SummonAction>, Mutex<Receiver<SummonAction>>) {
    static PAIR: OnceLock<(SyncSender<SummonAction>, Mutex<Receiver<SummonAction>>)> =
        OnceLock::new();
    PAIR.get_or_init(|| {
        let (tx, rx) = mpsc::sync_channel(16);
        (tx, Mutex::new(rx))
    })
}

fn action_sender() -> SyncSender<SummonAction> {
    action_pair().0.clone()
}

#[cfg(unix)]
fn install_unix() -> Result<SummonServer, SummonError> {
    use std::os::unix::net::UnixListener;

    let path = default_socket_path().ok_or(SummonError::NoPath)?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    if path.exists() {
        // Stale socket from a dead process: remove so bind can succeed.
        let _ = std::fs::remove_file(&path);
    }
    let listener = UnixListener::bind(&path)?;
    // Restrict to the user (runtime dir is usually already 0700).
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    }
    let _ = action_sender();
    let path_log = path.clone();
    let join = thread::Builder::new()
        .name("groket-hud-summon".into())
        .spawn(move || accept_loop(listener))
        .map_err(|err| SummonError::Other(format!("spawn summon thread: {err}")))?;
    crate::log::info(&format!("summon socket {}", path_log.display()));
    Ok(SummonServer {
        path,
        join: Some(join),
    })
}

#[cfg(unix)]
fn accept_loop(listener: std::os::unix::net::UnixListener) {
    let tx = action_sender();
    loop {
        let Ok((stream, _)) = listener.accept() else {
            thread::sleep(Duration::from_millis(50));
            continue;
        };
        if let Some(action) = read_action(stream) {
            if tx.send(action).is_err() {
                break;
            }
        }
    }
}

#[cfg(unix)]
fn read_action(stream: std::os::unix::net::UnixStream) -> Option<SummonAction> {
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let mut reader = BufReader::new(stream);
    let mut line = String::new();
    reader.read_line(&mut line).ok()?;
    parse_command(&line)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_command_words() {
        assert_eq!(parse_command("show"), Some(SummonAction::Show));
        assert_eq!(parse_command(" HIDE\n"), Some(SummonAction::Hide));
        assert_eq!(parse_command("Toggle"), Some(SummonAction::Toggle));
        assert_eq!(parse_command("quit"), None);
        assert_eq!(parse_command(""), None);
    }

    #[test]
    fn command_word_round_trip() {
        for action in [SummonAction::Show, SummonAction::Hide, SummonAction::Toggle] {
            assert_eq!(parse_command(command_word(action)), Some(action));
        }
    }

    #[test]
    fn default_path_uses_runtime_or_home() {
        // Env may or may not be set in CI; only require a path with the socket name.
        if let Some(p) = default_socket_path() {
            assert!(p.file_name().is_some_and(|n| n == "hud-summon.sock"));
        }
    }

    #[cfg(unix)]
    #[test]
    fn send_round_trip_on_temp_socket() {
        use std::os::unix::net::UnixListener;
        use std::sync::mpsc;

        let dir = std::env::temp_dir().join(format!("groket-hud-summon-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let path = dir.join("hud-summon.sock");
        let _ = std::fs::remove_file(&path);
        let listener = UnixListener::bind(&path).expect("bind");
        let (tx, rx) = mpsc::sync_channel(1);
        let path_server = path.clone();
        let handle = thread::spawn(move || {
            let (stream, _) = listener.accept().expect("accept");
            let action = read_action(stream).expect("action");
            tx.send(action).unwrap();
            let _ = std::fs::remove_file(&path_server);
        });
        send_command_to(&path, SummonAction::Toggle).expect("send");
        let got = rx.recv_timeout(Duration::from_secs(2)).expect("recv");
        assert_eq!(got, SummonAction::Toggle);
        handle.join().unwrap();
        let _ = std::fs::remove_dir_all(&dir);
    }
}

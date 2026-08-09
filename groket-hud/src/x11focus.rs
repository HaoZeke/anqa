//! X11 focus + keyboard grab for override-redirect palette windows.

use x11rb::connection::Connection;
use x11rb::protocol::xproto::{
    ConfigureWindowAux, ConnectionExt, GrabMode, GrabStatus, InputFocus, StackMode,
};

fn window_id(xid: u64) -> Option<u32> {
    let win = u32::try_from(xid).unwrap_or(0);
    (win != 0).then_some(win)
}

/// Raise, focus, and grab the keyboard so typing goes to the palette.
///
/// Returns false when the window is not viewable yet (caller should retry).
pub fn focus_window(xid: u64) -> bool {
    let Some(win) = window_id(xid) else {
        return false;
    };
    let Ok((conn, _screen)) = x11rb::connect(None) else {
        return false;
    };
    let _ = conn.configure_window(win, &ConfigureWindowAux::new().stack_mode(StackMode::ABOVE));
    let _ = conn.set_input_focus(InputFocus::PARENT, win, x11rb::CURRENT_TIME);
    let Ok(cookie) = conn.grab_keyboard(
        false,
        win,
        x11rb::CURRENT_TIME,
        GrabMode::ASYNC,
        GrabMode::ASYNC,
    ) else {
        let _ = conn.flush();
        return false;
    };
    let ok = match cookie.reply() {
        Ok(reply) => {
            reply.status == GrabStatus::SUCCESS || reply.status == GrabStatus::ALREADY_GRABBED
        }
        Err(_) => false,
    };
    let _ = conn.flush();
    ok
}

/// Drop the palette keyboard grab (hide / window mode).
pub fn release_keyboard() {
    let Ok((conn, _screen)) = x11rb::connect(None) else {
        return;
    };
    let _ = conn.ungrab_keyboard(x11rb::CURRENT_TIME);
    let _ = conn.flush();
}

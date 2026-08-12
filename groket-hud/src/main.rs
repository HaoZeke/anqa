//! groket-hud — iced desktop palette (control client).

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    groket_hud::log::install_panic_hook();
    #[cfg(target_os = "macos")]
    groket_hud::app::set_macos_accessory();
    let code = match groket_hud::run() {
        Ok(()) => 0,
        Err(err) => {
            eprintln!("groket-hud: {err}");
            1
        }
    };
    // Tray and notify threads outlive the iced loop.
    std::process::exit(code);
}

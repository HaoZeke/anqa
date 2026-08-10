//! groket-hud — iced desktop palette (control client).

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() -> iced::Result {
    groket_hud::log::install_panic_hook();
    #[cfg(target_os = "macos")]
    groket_hud::app::set_macos_accessory();
    groket_hud::run()
}

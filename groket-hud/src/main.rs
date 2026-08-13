//! groket-hud — iced desktop palette (control client).

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    groket_hud::log::install_panic_hook();
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.iter().any(|a| a == "--install-desktop") {
        let code = match groket_hud::install_desktop::run_cli() {
            Ok(_) => 0,
            Err(err) => {
                eprintln!("groket-hud: {err}");
                1
            }
        };
        std::process::exit(code);
    }
    if args.iter().any(|a| a == "--help" || a == "-h") {
        eprintln!(
            "groket-hud — session palette (control client)\n\
             \n\
             Options:\n\
               --install-desktop   Write user-local icons and a launcher entry\n\
                                   (Linux .desktop, macOS ~/Applications app,\n\
                                   Windows Start Menu shortcut). No system package.\n\
               --show              Show the palette (running HUD via summon socket)\n\
               --hide              Hide the overlay (running HUD)\n\
               --toggle            Show or hide (running HUD; Sway bind target).
                                   Forwards XDG_ACTIVATION_TOKEN to the HUD.\n\
               -h, --help          Show this help\n\
             \n\
             With no options, starts the HUD (tray; X11 summon hotkey when available).\n\
             Wayland: use --show/--toggle, tray Show HUD, or a compositor bind."
        );
        std::process::exit(0);
    }
    if let Some(action) = cli_summon_action(&args) {
        let code = match groket_hud::summon::send_command(action) {
            Ok(()) => 0,
            Err(err) => {
                eprintln!("groket-hud: {err}");
                1
            }
        };
        std::process::exit(code);
    }
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

fn cli_summon_action(args: &[String]) -> Option<groket_hud::summon::SummonAction> {
    for a in args {
        match a.as_str() {
            "--show" => return Some(groket_hud::summon::SummonAction::Show),
            "--hide" => return Some(groket_hud::summon::SummonAction::Hide),
            "--toggle" => return Some(groket_hud::summon::SummonAction::Toggle),
            _ => {}
        }
    }
    None
}

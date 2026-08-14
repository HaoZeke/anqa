//! groket-hud — iced desktop palette (control client).

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    groket_hud::log::install_panic_hook();
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.iter().any(|a| a == "--install-desktop") {
        let code = match groket_hud::install_desktop::run_cli() {
            Ok(_) => 0,
            Err(err) => {
                eprintln!("groket: {err}");
                1
            }
        };
        std::process::exit(code);
    }
    if args.iter().any(|a| a == "--help" || a == "-h") {
        eprintln!(
            "groket — session palette (control client)\n\
             \n\
             Options:\n\
               --install-desktop   Write user-local icons and a launcher entry\n\
                                   (Linux .desktop, macOS ~/Applications/groket.app,\n\
                                   Windows Start Menu shortcut). No system package.\n\
               --show              Show the palette. Starts groket when nothing is running.\n\
               --hide              Hide the overlay (running groket)\n\
               --toggle            Show or hide (running groket; Sway bind target).
                                   Forwards XDG_ACTIVATION_TOKEN to the palette.\n\
               -h, --help          Show this help\n\
             \n\
             With no options, starts groket (tray; X11 summon hotkey when available).\n\
             Wayland: use --show/--toggle, tray Show, or a compositor bind."
        );
        std::process::exit(0);
    }
    if let Some(action) = cli_summon_action(&args) {
        match groket_hud::summon::plan_summon_cli(action, groket_hud::summon::send_command(action))
        {
            Ok(groket_hud::summon::SummonCli::Done) => std::process::exit(0),
            Ok(groket_hud::summon::SummonCli::StartShown) => {
                std::env::set_var(groket_hud::tray::SHOW_ON_START_ENV, "1");
            }
            Err(err) => {
                eprintln!("groket: {err}");
                std::process::exit(1);
            }
        }
    }
    #[cfg(target_os = "macos")]
    groket_hud::macoswin::prepare_host();
    let code = match groket_hud::run() {
        Ok(()) => 0,
        Err(err) => {
            eprintln!("groket: {err}");
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

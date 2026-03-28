mod commands;

use tauri::{
    Emitter,
    Manager,
    menu::{MenuBuilder, MenuItemBuilder, PredefinedMenuItem, SubmenuBuilder},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};
use tauri::WindowEvent;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_positioner::init())
        .plugin(tauri_plugin_window_state::Builder::new().build())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    if event.state() == ShortcutState::Pressed {
                        let alt_space =
                            Shortcut::new(Some(Modifiers::ALT), Code::Space);
                        if shortcut == &alt_space {
                            if let Some(window) = app.get_webview_window("main") {
                                if window.is_visible().unwrap_or(false) {
                                    let _ = window.hide();
                                } else {
                                    let _ = window.show();
                                    let _ = window.set_focus();
                                }
                            }
                        }
                    }
                })
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            commands::config::read_operator_config,
            commands::config::read_trust_config,
            commands::config::check_onboarding_status,
            commands::config::write_config,
            commands::vault::list_vault_files,
            commands::vault::read_vault_file,
            commands::vault::search_vault,
            commands::system::get_service_status,
            commands::system::restart_service,
            commands::system::get_health,
            commands::agents::list_agents,
            commands::agents::get_agent,
            commands::work::run_work_command,
        ])
        .setup(|app| {
            // --- Register Global Shortcut: Option+Space ---
            let shortcut = Shortcut::new(Some(Modifiers::ALT), Code::Space);
            app.global_shortcut().register(shortcut)?;

            // --- System Tray ---
            let open_item =
                MenuItemBuilder::with_id("open", "Open Mission Control").build(app)?;
            let separator1 = PredefinedMenuItem::separator(app)?;
            let services_item = MenuItemBuilder::with_id("services", "Services: checking...")
                .enabled(false)
                .build(app)?;
            let separator2 = PredefinedMenuItem::separator(app)?;
            let ping_item =
                MenuItemBuilder::with_id("ping_agent", "Ping Agent").build(app)?;
            let separator3 = PredefinedMenuItem::separator(app)?;
            let quit_item = MenuItemBuilder::with_id("quit", "Quit").build(app)?;

            let tray_menu = MenuBuilder::new(app)
                .item(&open_item)
                .item(&separator1)
                .item(&services_item)
                .item(&separator2)
                .item(&ping_item)
                .item(&separator3)
                .item(&quit_item)
                .build()?;

            let handle_for_tray = app.handle().clone();
            let handle_for_menu = app.handle().clone();

            TrayIconBuilder::with_id("main-tray")
                .menu(&tray_menu)
                .tooltip("AOS Mission Control")
                .on_tray_icon_event(move |_tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        if let Some(window) = handle_for_tray.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                })
                .on_menu_event(move |app_handle, event| match event.id().as_ref() {
                    "open" => {
                        if let Some(window) = handle_for_menu.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "ping_agent" => {
                        let _ = app_handle.emit("tray-ping-agent", ());
                    }
                    "quit" => {
                        app_handle.exit(0);
                    }
                    _ => {}
                })
                .build(app)?;

            // --- Native macOS Menu Bar ---
            let app_submenu = SubmenuBuilder::new(app, "Mission Control")
                .about(None)
                .separator()
                .services()
                .separator()
                .hide()
                .hide_others()
                .show_all()
                .separator()
                .quit()
                .build()?;

            let new_task = MenuItemBuilder::with_id("new_task", "New Task")
                .accelerator("CmdOrCtrl+N")
                .build(app)?;
            let file_submenu = SubmenuBuilder::new(app, "File")
                .item(&new_task)
                .separator()
                .close_window()
                .build()?;

            let sidebar_toggle =
                MenuItemBuilder::with_id("sidebar_toggle", "Toggle Sidebar")
                    .accelerator("CmdOrCtrl+\\")
                    .build(app)?;
            let command_palette =
                MenuItemBuilder::with_id("command_palette", "Command Palette")
                    .accelerator("CmdOrCtrl+K")
                    .build(app)?;
            let view_submenu = SubmenuBuilder::new(app, "View")
                .item(&sidebar_toggle)
                .item(&command_palette)
                .separator()
                .fullscreen()
                .build()?;

            let screen_1 = MenuItemBuilder::with_id("go_screen_1", "Dashboard")
                .accelerator("CmdOrCtrl+1")
                .build(app)?;
            let screen_2 = MenuItemBuilder::with_id("go_screen_2", "Tasks")
                .accelerator("CmdOrCtrl+2")
                .build(app)?;
            let screen_3 = MenuItemBuilder::with_id("go_screen_3", "Agents")
                .accelerator("CmdOrCtrl+3")
                .build(app)?;
            let screen_4 = MenuItemBuilder::with_id("go_screen_4", "Vault")
                .accelerator("CmdOrCtrl+4")
                .build(app)?;
            let screen_5 = MenuItemBuilder::with_id("go_screen_5", "Services")
                .accelerator("CmdOrCtrl+5")
                .build(app)?;
            let screen_6 = MenuItemBuilder::with_id("go_screen_6", "Config")
                .accelerator("CmdOrCtrl+6")
                .build(app)?;
            let screen_7 = MenuItemBuilder::with_id("go_screen_7", "Logs")
                .accelerator("CmdOrCtrl+7")
                .build(app)?;
            let screen_8 = MenuItemBuilder::with_id("go_screen_8", "Search")
                .accelerator("CmdOrCtrl+8")
                .build(app)?;
            let screen_9 = MenuItemBuilder::with_id("go_screen_9", "Settings")
                .accelerator("CmdOrCtrl+9")
                .build(app)?;

            let go_submenu = SubmenuBuilder::new(app, "Go")
                .item(&screen_1)
                .item(&screen_2)
                .item(&screen_3)
                .item(&screen_4)
                .item(&screen_5)
                .item(&screen_6)
                .item(&screen_7)
                .item(&screen_8)
                .item(&screen_9)
                .build()?;

            let window_submenu = SubmenuBuilder::new(app, "Window")
                .minimize()
                .separator()
                .close_window()
                .build()?;

            let menu = MenuBuilder::new(app)
                .item(&app_submenu)
                .item(&file_submenu)
                .item(&view_submenu)
                .item(&go_submenu)
                .item(&window_submenu)
                .build()?;

            app.set_menu(menu)?;

            // Handle menu events — forward to frontend via events.
            let menu_handle = app.handle().clone();
            app.on_menu_event(move |_app_handle, event| {
                let id = event.id().as_ref();
                match id {
                    "new_task" | "sidebar_toggle" | "command_palette" => {
                        let _ = menu_handle.emit(&format!("menu-{id}"), ());
                    }
                    s if s.starts_with("go_screen_") => {
                        let _ = menu_handle.emit("menu-navigate", s);
                    }
                    _ => {}
                }
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            // ⌘W hides to tray instead of quitting
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Mission Control");
}

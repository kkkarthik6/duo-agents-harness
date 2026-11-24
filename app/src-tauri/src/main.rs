// Duo is a window onto the local engine at http://127.0.0.1:8787.
// Opening the app starts a fresh engine; quitting it stops the engine and every agent.
// The engine is the duo command inside this app's own bundle (Contents/Resources/_duo/bin/duo).
// A build run from source falls back to the path baked in at build time.
use std::path::PathBuf;
use std::process::Command;

fn duo_bin() -> PathBuf {
    if let Ok(exe) = std::env::current_exe() {
        if let Some(contents) = exe.parent().and_then(|p| p.parent()) {
            let bundled = contents.join("Resources/_duo/bin/duo");
            if bundled.exists() {
                return bundled;
            }
        }
    }
    PathBuf::from(env!("DUO_BIN"))
}

fn duo(arg: &str) {
    let _ = Command::new("/usr/bin/env").arg("python3").arg(duo_bin()).arg(arg).status();
}

fn main() {
    duo("serve-bg");
    tauri::Builder::default()
        .build(tauri::generate_context!())
        .expect("Duo could not open its window")
        .run(|_app, event| {
            if let tauri::RunEvent::Exit = event {
                duo("quit");
            }
        });
}

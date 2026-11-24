fn main() {
    // The app starts the engine through the duo command that sits next to it in _duo/bin.
    let duo = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../bin/duo");
    let duo = duo.canonicalize().expect("_duo/bin/duo not found");
    println!("cargo:rustc-env=DUO_BIN={}", duo.display());
    println!("cargo:rerun-if-changed=../../bin/duo");
    tauri_build::build()
}

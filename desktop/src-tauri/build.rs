fn main() {
    // Listing the app's commands generates a permission for each (allow-app-info, ...), so capabilities can grant
    // the studio page (served by the local engine) just the one command it needs.
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(&[
        "app_info",
        "detect_hardware",
        "install_engine",
        "start_studio",
        "rerun_setup",
        "open_external",
        "move_to_applications",
    ])))
    .expect("failed to run tauri-build");
}

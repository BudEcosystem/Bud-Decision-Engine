// Bud Decision Studio desktop app.
//
// The window first shows the bundled setup screens (desktop/installer). They ask this process to detect the hardware
// and install the engine: a private Python environment with the PyTorch build for the chosen processor, created by
// installer/engine.py through the bundled `uv`. Once installed, the app starts the studio server from that
// environment on 127.0.0.1 and points the window at it. Closing the app stops the server, which ejects every model.
//
// Files: the engine's code ships inside the app (resources/engine); everything the app writes lives in the
// per-user app data folder: engine-env/ (Python + PyTorch), data/ (settings, activity log), logs/.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter, Manager, RunEvent, State, Url, WebviewUrl, WebviewWindowBuilder};

const PREFERRED_PORT: u16 = 8420;

#[derive(Default)]
struct Studio {
    server: Mutex<Option<Child>>,
    url: Mutex<Option<String>>,
    home: Mutex<Option<Url>>,
}

// ------------------------------------------------------------------------------------------------------------------
// paths

fn data_root(app: &AppHandle) -> PathBuf {
    app.path().app_data_dir().expect("no app data folder")
}

/// The engine's source (basal/, ui/, installer/, requirements*.txt). BUD_STUDIO_ENGINE points at a checkout while
/// developing the app.
fn engine_dir(app: &AppHandle) -> PathBuf {
    if let Ok(p) = std::env::var("BUD_STUDIO_ENGINE") {
        return PathBuf::from(p);
    }
    app.path().resource_dir().expect("no resource folder").join("engine")
}

fn venv_dir(app: &AppHandle) -> PathBuf {
    data_root(app).join("engine-env")
}

fn studio_data(app: &AppHandle) -> PathBuf {
    if let Ok(p) = std::env::var("BUD_STUDIO_DATA") {
        return PathBuf::from(p);
    }
    data_root(app).join("data")
}

fn python(app: &AppHandle) -> PathBuf {
    if let Ok(p) = std::env::var("BUD_STUDIO_PYTHON") {
        return PathBuf::from(p);
    }
    let v = venv_dir(app);
    if cfg!(windows) { v.join("Scripts").join("python.exe") } else { v.join("bin").join("python") }
}

/// The uv binary bundled next to the app's executable (a Tauri sidecar), falling back to one on PATH.
fn uv_path() -> PathBuf {
    let name = if cfg!(windows) { "bud-uv.exe" } else { "bud-uv" };
    if let Some(dir) = std::env::current_exe().ok().and_then(|p| p.parent().map(Path::to_path_buf)) {
        let p = dir.join(name);
        if p.exists() {
            return p;
        }
    }
    PathBuf::from(if cfg!(windows) { "uv.exe" } else { "uv" })
}

/// Variables an AppImage's launcher points at the AppImage's own files. Processes the app starts (Python, uv, the
/// browser) must see the computer's normal environment, or Python cannot even find its standard library.
const APPIMAGE_VARS: &[&str] = &[
    "LD_LIBRARY_PATH", "PERLLIB", "QT_PLUGIN_PATH", "GST_PLUGIN_SYSTEM_PATH", "GST_PLUGIN_SYSTEM_PATH_1_0",
    "GDK_PIXBUF_MODULE_FILE", "GTK_DATA_PREFIX", "GTK_EXE_PREFIX", "GTK_PATH", "GTK_IM_MODULE_FILE", "GTK_THEME",
    "GIO_MODULE_DIR", "GSETTINGS_SCHEMA_DIR", "GI_TYPELIB_PATH",
];

/// A command with a clean environment that never flashes a console window on Windows.
fn command(program: impl AsRef<std::ffi::OsStr>) -> Command {
    #[allow(unused_mut)]
    let mut c = Command::new(program);
    c.env_remove("PYTHONHOME").env_remove("PYTHONPATH");
    if let Ok(appdir) = std::env::var("APPDIR") {
        for k in APPIMAGE_VARS {
            c.env_remove(k);
        }
        for k in ["PATH", "XDG_DATA_DIRS"] {
            if let Ok(v) = std::env::var(k) {
                let kept: Vec<&str> = v.split(':').filter(|p| !p.is_empty() && !p.starts_with(&appdir)).collect();
                c.env(k, kept.join(":"));
            }
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        c.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    c
}

fn engine_cmd(app: &AppHandle, args: &[&str]) -> Command {
    let mut c = command(uv_path());
    c.args(["run", "--no-project", "--python", "3.12"])
        .arg(engine_dir(app).join("installer").join("engine.py"))
        .args(args)
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .stdin(Stdio::null());
    c
}

fn read_config(app: &AppHandle) -> Option<Value> {
    let text = std::fs::read_to_string(studio_data(app).join("config.json")).ok()?;
    serde_json::from_str(&text).ok()
}

// ------------------------------------------------------------------------------------------------------------------
// commands used by the setup screens

fn can_move_to_applications() -> bool {
    #[cfg(target_os = "macos")]
    return bundle_outside_applications().is_some();
    #[cfg(not(target_os = "macos"))]
    return false;
}

#[tauri::command]
fn app_info(app: AppHandle) -> Value {
    let cfg = read_config(&app);
    let installed = cfg.is_some() && python(&app).exists();
    json!({
        "version": app.package_info().version.to_string(),
        "data_dir": data_root(&app),
        "installed": installed,
        "config": cfg,
        "os": std::env::consts::OS,
        "arch": std::env::consts::ARCH,
        // Unattended setup for managed deployments: BUD_STUDIO_DEVICE=cuda|mps|xpu|rocm|cpu|recommended skips the questions.
        "unattended": std::env::var("BUD_STUDIO_DEVICE").ok(),
        "can_move_to_applications": can_move_to_applications(),
    })
}

#[tauri::command]
async fn detect_hardware(app: AppHandle) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let out = engine_cmd(&app, &["detect", "--json"]).output().map_err(|e| format!("Could not start the setup helper: {e}"))?;
        if !out.status.success() {
            return Err(format!("Checking this computer failed: {}", String::from_utf8_lossy(&out.stderr).trim()));
        }
        serde_json::from_slice(&out.stdout).map_err(|e| format!("Unexpected answer from the hardware check: {e}"))
    })
    .await
    .map_err(|e| e.to_string())?
}

/// Runs the installer, forwarding each progress line to the window as an "install" event.
#[tauri::command]
async fn install_engine(app: AppHandle, device: String) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let venv = venv_dir(&app);
        let data = studio_data(&app);
        let uv = uv_path();
        let args = ["install", "--device", &device, "--venv", &venv.to_string_lossy(), "--data", &data.to_string_lossy(),
                    "--uv", &uv.to_string_lossy()].map(String::from);
        let args: Vec<&str> = args.iter().map(String::as_str).collect();
        let mut child = engine_cmd(&app, &args)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| format!("Could not start the installer: {e}"))?;
        // uv reports its own work (such as fetching Python) on stderr; show it in the details log.
        let err = child.stderr.take().unwrap();
        let app2 = app.clone();
        let relay = std::thread::spawn(move || {
            for line in BufReader::new(err).lines().map_while(Result::ok) {
                let _ = app2.emit("install", json!({"type": "log", "line": line}));
            }
        });
        let mut result: Result<Value, String> = Err("The installer stopped without finishing.".into());
        for line in BufReader::new(child.stdout.take().unwrap()).lines().map_while(Result::ok) {
            let Ok(ev) = serde_json::from_str::<Value>(&line) else {
                let _ = app.emit("install", json!({"type": "log", "line": line}));
                continue;
            };
            match ev["type"].as_str() {
                Some("done") => result = Ok(ev["config"].clone()),
                Some("error") => result = Err(ev["message"].as_str().unwrap_or("Setup failed.").to_string()),
                _ => {}
            }
            let _ = app.emit("install", ev);
        }
        let _ = child.wait();
        let _ = relay.join();
        result
    })
    .await
    .map_err(|e| e.to_string())?
}

fn port_free(port: u16) -> bool {
    TcpListener::bind(("127.0.0.1", port)).is_ok()
}

fn free_port() -> u16 {
    if port_free(PREFERRED_PORT) {
        return PREFERRED_PORT;
    }
    TcpListener::bind(("127.0.0.1", 0)).and_then(|l| l.local_addr()).map(|a| a.port()).unwrap_or(PREFERRED_PORT + 1)
}

/// True once the studio answers GET /api/config with 200.
fn studio_up(port: u16) -> bool {
    let Ok(mut s) = TcpStream::connect_timeout(&([127, 0, 0, 1], port).into(), Duration::from_millis(400)) else { return false };
    let _ = s.set_read_timeout(Some(Duration::from_secs(3)));
    if s.write_all(b"GET /api/config HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n").is_err() {
        return false;
    }
    let mut buf = [0u8; 16];
    matches!(s.read(&mut buf), Ok(n) if n >= 12 && &buf[9..12] == b"200")
}

fn log_tail(path: &Path, lines: usize) -> String {
    let text = std::fs::read_to_string(path).unwrap_or_default();
    let all: Vec<&str> = text.lines().collect();
    all[all.len().saturating_sub(lines)..].join("\n")
}

fn stop_server(state: &Studio) {
    if let Some(mut child) = state.server.lock().unwrap().take() {
        // SIGTERM lets the server eject every model before it exits.
        #[cfg(unix)]
        let _ = command("kill").args(["-TERM", &child.id().to_string()]).status();
        #[cfg(windows)]
        let _ = command("taskkill").args(["/PID", &child.id().to_string(), "/T"]).status();
        let t = Instant::now();
        while t.elapsed() < Duration::from_secs(8) {
            if matches!(child.try_wait(), Ok(Some(_))) {
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        if matches!(child.try_wait(), Ok(None)) {
            #[cfg(windows)]
            let _ = command("taskkill").args(["/PID", &child.id().to_string(), "/T", "/F"]).status();
            let _ = child.kill();
        }
        let _ = child.wait();
    }
    *state.url.lock().unwrap() = None;
}

/// Starts the studio server from the installed engine and shows it in the window.
#[tauri::command]
async fn start_studio(app: AppHandle, state: State<'_, Studio>) -> Result<String, String> {
    let running = state.url.lock().unwrap().clone();
    let url = match running {
        Some(u) => u,
        None => {
            let py = python(&app);
            if !py.exists() {
                return Err("The engine is not installed yet.".into());
            }
            let port = free_port();
            let logs = data_root(&app).join("logs");
            std::fs::create_dir_all(&logs).map_err(|e| e.to_string())?;
            let log_path = logs.join("studio.log");
            let log = std::fs::File::create(&log_path).map_err(|e| e.to_string())?;
            let child = command(&py)
                .args(["-m", "basal.server", "--port", &port.to_string()])
                .current_dir(engine_dir(&app))
                .env("BASAL_DATA", studio_data(&app))
                .env("BASAL_PARENT_PID", std::process::id().to_string())
                .env("PYTHONUNBUFFERED", "1")
                .env("PYTHONDONTWRITEBYTECODE", "1")
                .stdin(Stdio::null())
                .stdout(log.try_clone().map_err(|e| e.to_string())?)
                .stderr(log)
                .spawn()
                .map_err(|e| format!("Could not start the studio: {e}"))?;
            *state.server.lock().unwrap() = Some(child);
            // Wait for it to answer. The first start imports PyTorch, which can take a while on a cold disk.
            let t = Instant::now();
            loop {
                let up = tauri::async_runtime::spawn_blocking(move || studio_up(port)).await.unwrap_or(false);
                if up {
                    break;
                }
                let exited = state.server.lock().unwrap().as_mut().map(|c| matches!(c.try_wait(), Ok(Some(_)))).unwrap_or(true);
                if exited || t.elapsed() > Duration::from_secs(180) {
                    stop_server(&state);
                    return Err(format!("The studio did not start.\n\n{}", log_tail(&log_path, 30)));
                }
                tokio_sleep(Duration::from_millis(300)).await;
            }
            let u = format!("http://127.0.0.1:{port}/");
            *state.url.lock().unwrap() = Some(u.clone());
            u
        }
    };
    let win = app.get_webview_window("main").ok_or("The window is gone.")?;
    win.navigate(Url::parse(&url).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    Ok(url)
}

async fn tokio_sleep(d: Duration) {
    let _ = tauri::async_runtime::spawn_blocking(move || std::thread::sleep(d)).await;
}

/// From the studio's System page: stop the studio and show setup again, to move models to another processor.
#[tauri::command]
async fn rerun_setup(app: AppHandle, state: State<'_, Studio>) -> Result<(), String> {
    stop_server(&state);
    let home = state.home.lock().unwrap().clone().ok_or("Setup page unknown")?;
    let mut u = home.clone();
    u.set_query(Some("setup=again"));
    app.get_webview_window("main").ok_or("The window is gone.")?.navigate(u).map_err(|e| e.to_string())
}

/// Opens a web link in the person's browser instead of inside the app.
#[tauri::command]
fn open_external(url: String) -> Result<(), String> {
    open_in_browser(&url)
}

fn open_in_browser(url: &str) -> Result<(), String> {
    let u = Url::parse(url).map_err(|e| e.to_string())?;
    if u.scheme() != "https" && u.scheme() != "http" {
        return Err("Only web links can be opened.".into());
    }
    #[cfg(target_os = "macos")]
    let r = command("open").arg(u.as_str()).spawn();
    #[cfg(windows)]
    let r = command("rundll32").args(["url.dll,FileProtocolHandler", u.as_str()]).spawn();
    #[cfg(all(unix, not(target_os = "macos")))]
    let r = command("xdg-open").arg(u.as_str()).spawn();
    r.map(|_| ()).map_err(|e| e.to_string())
}

// ------------------------------------------------------------------------------------------------------------------
// app launcher

/// Linux AppImage: add Bud Decision Studio to the applications menu with its icon, the way the .deb and .rpm do.
/// Rewritten whenever the AppImage has moved, so the launcher always opens the current file.
#[cfg(target_os = "linux")]
fn install_launcher() {
    let Ok(appimage) = std::env::var("APPIMAGE") else { return };
    let home = std::env::var("HOME").unwrap_or_default();
    let data = std::env::var("XDG_DATA_HOME").ok().filter(|d| !d.is_empty()).map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(&home).join(".local/share"));
    let icons: [(&str, &[u8]); 4] = [
        ("32x32", include_bytes!("../icons/32x32.png")),
        ("128x128", include_bytes!("../icons/128x128.png")),
        ("256x256", include_bytes!("../icons/128x128@2x.png")),
        ("512x512", include_bytes!("../icons/icon.png")),
    ];
    for (size, bytes) in icons {
        let dir = data.join("icons/hicolor").join(size).join("apps");
        let path = dir.join("bud-decision-studio.png");
        if std::fs::read(&path).map(|b| b != bytes).unwrap_or(true) {
            let _ = std::fs::create_dir_all(&dir).and_then(|_| std::fs::write(&path, bytes));
        }
    }
    let entry = format!(
        "[Desktop Entry]\nType=Application\nName=Bud Decision Studio\nGenericName=Decision model studio\n\
         Comment=Run open decision models on your own computer\nExec=\"{appimage}\" %U\nIcon=bud-decision-studio\n\
         Terminal=false\nCategories=Development;\nKeywords=AI;decision;model;LLM;Jev;\n\
         StartupWMClass=bud-decision-studio\nX-AppImage-Path={appimage}\n"
    );
    let apps = data.join("applications");
    let path = apps.join("bud-decision-studio.desktop");
    if std::fs::read_to_string(&path).map(|t| t != entry).unwrap_or(true) {
        if std::fs::create_dir_all(&apps).and_then(|_| std::fs::write(&path, &entry)).is_ok() {
            let _ = command("update-desktop-database").arg(&apps).status();
            let _ = command("gtk-update-icon-cache").args(["-q", "-t"]).arg(data.join("icons/hicolor")).status();
        }
    }
}

/// macOS: where the app bundle is, if it is not already in an Applications folder (for example still on the disk
/// image or in Downloads).
#[cfg(target_os = "macos")]
fn bundle_outside_applications() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let bundle = exe.ancestors().find(|p| p.extension().map(|e| e == "app").unwrap_or(false))?.to_path_buf();
    let s = bundle.to_string_lossy();
    if s.starts_with("/Applications/") || s.contains("/Applications/") { None } else { Some(bundle) }
}

/// macOS: copy the app into /Applications (or ~/Applications when that is not writable), open the copy, and quit.
#[tauri::command]
fn move_to_applications(app: AppHandle) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        let bundle = bundle_outside_applications().ok_or("The app is already in Applications.")?;
        let name = bundle.file_name().ok_or("Unknown app name")?.to_owned();
        let system = PathBuf::from("/Applications");
        let target_dir = if std::fs::metadata(&system).map(|m| !m.permissions().readonly()).unwrap_or(false)
            && command("test").arg("-w").arg(&system).status().map(|s| s.success()).unwrap_or(false) {
            system
        } else {
            PathBuf::from(std::env::var("HOME").unwrap_or_default()).join("Applications")
        };
        std::fs::create_dir_all(&target_dir).map_err(|e| e.to_string())?;
        let target = target_dir.join(name);
        let _ = std::fs::remove_dir_all(&target);
        let ok = command("ditto").arg(&bundle).arg(&target).status().map(|s| s.success()).unwrap_or(false);
        if !ok {
            return Err("Could not copy the app into Applications.".into());
        }
        let _ = command("xattr").args(["-dr", "com.apple.quarantine"]).arg(&target).status();
        command("open").arg("-n").arg(&target).spawn().map_err(|e| e.to_string())?;
        app.exit(0);
        Ok(())
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = app;
        Err("Only needed on macOS.".into())
    }
}

fn is_local(u: &Url) -> bool {
    matches!(u.scheme(), "tauri" | "asset" | "about" | "data" | "blob")
        || matches!(u.host_str(), Some("127.0.0.1") | Some("localhost") | Some("tauri.localhost"))
}

fn main() {
    // WebKitGTK's DMA-BUF renderer draws a blank window on many NVIDIA Linux systems (the GB10 included), so use its
    // shared-memory path unless the person has chosen otherwise.
    #[cfg(target_os = "linux")]
    if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
    }
    let app = tauri::Builder::default()
        .manage(Studio::default())
        .invoke_handler(tauri::generate_handler![app_info, detect_hardware, install_engine, start_studio, rerun_setup, open_external,
                                                 move_to_applications])
        .setup(|app| {
            #[cfg(target_os = "linux")]
            install_launcher();
            // Size the window to the screen: roomy on a desktop monitor, never larger than a laptop display.
            let (mut w, mut h) = (1440.0, 920.0);
            if let Ok(Some(m)) = app.primary_monitor() {
                let s = m.size().to_logical::<f64>(m.scale_factor());
                w = f64::min(w, s.width * 0.92);
                h = f64::min(h, s.height * 0.9);
            }
            let win = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Bud Decision Studio")
                .inner_size(w, h)
                .min_inner_size(900.0, 620.0)
                .center()
                .on_navigation(|u| {
                    if is_local(u) {
                        return true;
                    }
                    let _ = open_in_browser(u.as_str());
                    false
                })
                .build()?;
            *app.state::<Studio>().home.lock().unwrap() = win.url().ok();
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Bud Decision Studio");
    app.run(|app, event| {
        if let RunEvent::Exit = event {
            stop_server(&app.state::<Studio>());
        }
    });
}

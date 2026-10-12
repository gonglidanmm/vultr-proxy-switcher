// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::{TcpStream, ToSocketAddrs, UdpSocket};
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::State;

/// 联通性检测：TCP 连接到 host:port，返回延迟毫秒数（超时/失败返回 Err）。
#[tauri::command]
fn check_connectivity(host: String, port: u16) -> Result<f64, String> {
    let addr_str = format!("{}:{}", host, port);
    let addrs: Vec<_> = addr_str
        .to_socket_addrs()
        .map_err(|e| format!("无法解析地址 {}: {}", addr_str, e))?
        .collect();
    let start = Instant::now();
    match TcpStream::connect_timeout(&addrs[0], Duration::from_secs(5)) {
        Ok(_stream) => {
            let ms = start.elapsed().as_millis() as f64;
            Ok(ms)
        }
        Err(e) => Err(format!("连接失败: {}", e)),
    }
}

/// UDP 端口探测：Hysteria2(QUIC) 用 UDP，TCP 测不到，改用 UDP 探测。
/// 发一个空包：收到 ICMP port unreachable（ConnectionRefused）= 端口关闭；超时 = 端口开放。
#[tauri::command]
fn check_udp(host: String, port: u16) -> Result<f64, String> {
    let addr_str = format!("{}:{}", host, port);
    let addrs: Vec<_> = addr_str
        .to_socket_addrs()
        .map_err(|e| format!("无法解析地址 {}: {}", addr_str, e))?
        .collect();
    let socket = UdpSocket::bind("0.0.0.0:0").map_err(|e| format!("绑定失败: {}", e))?;
    socket
        .set_read_timeout(Some(Duration::from_secs(2)))
        .ok();
    socket
        .connect(&addrs[0])
        .map_err(|e| format!("连接失败: {}", e))?;
    let start = Instant::now();
    socket.send(&[0u8; 8]).map_err(|e| format!("发送失败: {}", e))?;
    let mut buf = [0u8; 64];
    match socket.recv(&mut buf) {
        Ok(_) => Ok(start.elapsed().as_millis() as f64),
        Err(e) => {
            if e.kind() == std::io::ErrorKind::WouldBlock || e.kind() == std::io::ErrorKind::TimedOut {
                Ok(start.elapsed().as_millis() as f64)
            } else {
                Err(format!("端口不可达: {}", e))
            }
        }
    }
}

/// Windows 系统代理：WinINet 只支持 HTTP 代理，指向 sing-box 的 mixed 入站（HTTP+SOCKS5 同端口）。
#[cfg(target_os = "windows")]
fn set_windows_proxy(enable: bool, host: &str, port: u16) -> Result<(), String> {
    use windows_sys::Win32::Networking::WinInet::{
        InternetSetOptionW, INTERNET_OPTION_REFRESH, INTERNET_OPTION_SETTINGS_CHANGED,
    };
    let ps = format!("{}:{}", host, port);
    let reg_key = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings";
    if enable {
        let _ = Command::new("reg")
            .args(["add", reg_key, "/v", "ProxyEnable", "/t", "REG_DWORD", "/d", "1", "/f"])
            .output();
        let _ = Command::new("reg")
            .args(["add", reg_key, "/v", "ProxyServer", "/t", "REG_SZ", "/d", &ps, "/f"])
            .output();
    } else {
        let _ = Command::new("reg")
            .args(["add", reg_key, "/v", "ProxyEnable", "/t", "REG_DWORD", "/d", "0", "/f"])
            .output();
    }
    // 通知 WinINet 刷新（否则浏览器不立即生效）
    unsafe {
        InternetSetOptionW(
            std::ptr::null(),
            INTERNET_OPTION_SETTINGS_CHANGED,
            std::ptr::null(),
            0,
        );
        InternetSetOptionW(std::ptr::null(), INTERNET_OPTION_REFRESH, std::ptr::null(), 0);
    }
    Ok(())
}

/// 设置/关闭 macOS 系统代理（HTTP/HTTPS/SOCKS），指向 host:port。
#[tauri::command]
fn set_system_proxy(enable: bool, host: String, port: u16) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        let out = Command::new("networksetup")
            .args(["-listallnetworkservices"])
            .output()
            .map_err(|e| e.to_string())?;
        let text = String::from_utf8_lossy(&out.stdout);
        let port_str = port.to_string();
        let services: Vec<String> = text
            .lines()
            .skip(1)
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty() && !s.contains('*'))
            .collect();
        if services.is_empty() {
            return Err("未找到网络服务".to_string());
        }
        for service in services {
            if enable {
                let _ = Command::new("networksetup")
                    .args(["-setwebproxy", &service, &host, &port_str])
                    .output();
                let _ = Command::new("networksetup")
                    .args(["-setsecurewebproxy", &service, &host, &port_str])
                    .output();
                let _ = Command::new("networksetup")
                    .args(["-setsocksfirewallproxy", &service, &host, &port_str])
                    .output();
            } else {
                let _ = Command::new("networksetup")
                    .args(["-setwebproxystate", &service, "off"])
                    .output();
                let _ = Command::new("networksetup")
                    .args(["-setsecurewebproxystate", &service, "off"])
                    .output();
                let _ = Command::new("networksetup")
                    .args(["-setsocksfirewallproxystate", &service, "off"])
                    .output();
            }
        }
        Ok(())
    }
    #[cfg(target_os = "windows")]
    {
        set_windows_proxy(enable, &host, port)
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = (enable, host, port);
        Err("不支持的系统".to_string())
    }
}

/// 本地代理进程状态（sing-box 子进程句柄）。
struct ProxyState(Mutex<Option<Child>>);

fn config_dir() -> PathBuf {
    let dir = std::env::temp_dir().join("proxy-client");
    let _ = std::fs::create_dir_all(&dir);
    dir
}

fn sing_box_path() -> PathBuf {
    // 打包后：sidecar 二进制在主程序同目录（Tauri 打包时重命名为 sing-box[.exe]，去掉 target triple）
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let name = if cfg!(windows) { "sing-box.exe" } else { "sing-box" };
            let p = dir.join(name);
            if p.exists() {
                return p;
            }
        }
    }
    // dev 模式 fallback：CARGO_MANIFEST_DIR = client/src-tauri，../bin/sing-box = client/bin/sing-box
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../bin/sing-box")
}

/// 启动本地代理：写 sing-box 配置 + 启动进程（先停旧的）。
#[tauri::command]
fn start_proxy(config_json: String, state: State<ProxyState>) -> Result<(), String> {
    let cfg = config_dir().join("config.json");
    eprintln!("[start_proxy] config_path={:?}", cfg);
    eprintln!("[start_proxy] sing_box_path={:?}", sing_box_path());
    eprintln!("[start_proxy] config_json={}", &config_json[..config_json.len().min(200)]);
    std::fs::write(&cfg, &config_json).map_err(|e| format!("写配置失败: {e}"))?;

    let mut guard = state.0.lock().unwrap();
    if let Some(mut c) = guard.take() {
        let _ = c.kill();
        let _ = c.wait();
    }
    let mut cmd = Command::new(sing_box_path());
    cmd.args(["-c", cfg.to_str().unwrap_or_default(), "run"]);
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        // CREATE_NO_WINDOW = 0x08000000：sing-box 后台静默运行，不弹黑色控制台窗口
        cmd.creation_flags(0x08000000);
    }
    let child = cmd
        .spawn()
        .map_err(|e| format!("启动 sing-box 失败: {e}"))?;
    eprintln!("[start_proxy] spawn ok, pid={}", child.id());
    *guard = Some(child);
    Ok(())
}

/// 停止本地代理进程。
#[tauri::command]
fn stop_proxy(state: State<ProxyState>) -> Result<(), String> {
    let mut guard = state.0.lock().unwrap();
    if let Some(mut c) = guard.take() {
        let _ = c.kill();
        let _ = c.wait();
    }
    Ok(())
}

fn main() {
    // Windows 高 DPI：Per-Monitor V2（否则 125%/150% 缩放屏上界面模糊）
    #[cfg(target_os = "windows")]
    {
        use windows_sys::Win32::UI::HiDpi::{
            SetProcessDpiAwarenessContext, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
        };
        unsafe {
            let _ = SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        }
    }
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            // 已有实例在运行：聚焦它的主窗口，不再开第二个
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .manage(ProxyState(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![
            check_connectivity,
            check_udp,
            set_system_proxy,
            start_proxy,
            stop_proxy
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

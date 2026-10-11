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
    #[cfg(not(target_os = "macos"))]
    {
        let _ = (enable, host, port);
        Err("仅支持 macOS".to_string())
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
    // dev 模式：CARGO_MANIFEST_DIR = client/src-tauri，../bin/sing-box = client/bin/sing-box
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
    let child = Command::new(sing_box_path())
        .args(["-c", cfg.to_str().unwrap_or_default(), "run"])
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
    tauri::Builder::default()
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

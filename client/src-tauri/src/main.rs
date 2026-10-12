// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::{TcpStream, ToSocketAddrs, UdpSocket};
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, State};

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

/// 用真实 DNS（8.8.8.8:53，UDP）解析域名 A 记录，绕过本机翻墙的 fake-ip 劫持。
fn resolve_a_real(domain: &str) -> Result<Vec<String>, String> {
    let mut query = Vec::with_capacity(64);
    query.extend_from_slice(&[0x12, 0x34]); // Transaction ID
    query.extend_from_slice(&[0x01, 0x00]); // Flags: RD=1（标准递归查询）
    query.extend_from_slice(&[0x00, 0x01]); // QDCOUNT = 1
    query.extend_from_slice(&[0x00, 0x00]); // ANCOUNT = 0
    query.extend_from_slice(&[0x00, 0x00]); // NSCOUNT = 0
    query.extend_from_slice(&[0x00, 0x00]); // ARCOUNT = 0
    for label in domain.split('.') {
        let b = label.as_bytes();
        if b.is_empty() {
            return Err(format!("非法域名: {domain}"));
        }
        query.push(b.len() as u8);
        query.extend_from_slice(b);
    }
    query.push(0);
    query.extend_from_slice(&[0x00, 0x01]); // QTYPE = A
    query.extend_from_slice(&[0x00, 0x01]); // QCLASS = IN

    let socket = UdpSocket::bind("0.0.0.0:0").map_err(|e| format!("绑定失败: {e}"))?;
    socket
        .set_read_timeout(Some(Duration::from_secs(3)))
        .ok();
    socket
        .connect("8.8.8.8:53")
        .map_err(|e| format!("连接 DNS 失败: {e}"))?;
    socket.send(&query).map_err(|e| format!("发送失败: {e}"))?;
    let mut buf = [0u8; 512];
    let n = socket.recv(&mut buf).map_err(|e| format!("接收失败: {e}"))?;

    if n < 12 {
        return Err("DNS 响应过短".to_string());
    }
    let ancount = u16::from_be_bytes([buf[6], buf[7]]) as usize;
    // 跳过 header(12) + question（QNAME + QTYPE + QCLASS）
    let mut pos = 12;
    while pos < n && buf[pos] != 0 {
        pos += 1 + buf[pos] as usize;
    }
    pos += 1; // 终止 0
    pos += 4; // QTYPE + QCLASS

    let mut ips = Vec::new();
    for _ in 0..ancount {
        if pos >= n {
            break;
        }
        // 跳过 NAME（处理压缩指针 0xC0）
        if buf[pos] & 0xC0 == 0xC0 {
            pos += 2;
        } else {
            while pos < n && buf[pos] != 0 {
                pos += 1 + buf[pos] as usize;
            }
            pos += 1;
        }
        if pos + 10 > n {
            break;
        }
        let rtype = u16::from_be_bytes([buf[pos], buf[pos + 1]]);
        let rdlength = u16::from_be_bytes([buf[pos + 8], buf[pos + 9]]) as usize;
        pos += 10;
        if rtype == 1 && rdlength == 4 && pos + 4 <= n {
            ips.push(format!(
                "{}.{}.{}.{}",
                buf[pos],
                buf[pos + 1],
                buf[pos + 2],
                buf[pos + 3]
            ));
        }
        pos += rdlength;
    }
    if ips.is_empty() {
        Err("未解析到 A 记录".to_string())
    } else {
        Ok(ips)
    }
}

/// 域名解析检查：返回域名当前（真实 DNS）解析到的 IP，供客户端对比 node_data 的真实 IP。
#[tauri::command]
fn resolve_domain(domain: String) -> Result<String, String> {
    let ips = resolve_a_real(&domain)?;
    Ok(ips.join(", "))
}
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
    let app = tauri::Builder::default()
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
            resolve_domain,
            set_system_proxy,
            start_proxy,
            stop_proxy
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        // 退出时杀掉 sing-box 子进程，避免残留占用文件（导致 Windows 重装报「Error opening file for writing」）
        if let tauri::RunEvent::Exit = event {
            if let Some(state) = app_handle.try_state::<ProxyState>() {
                if let Ok(mut guard) = state.0.lock() {
                    if let Some(mut c) = guard.take() {
                        let _ = c.kill();
                        let _ = c.wait();
                    }
                }
            }
        }
    });
}

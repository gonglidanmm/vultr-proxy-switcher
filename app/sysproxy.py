"""macOS 系统代理控制（networksetup）。

实现 v2rayN 式三档系统代理：
- 不代理：关闭系统 SOCKS + 自动代理
- 代理（PAC）：设置系统自动代理，国内直连、国外走本地代理
- 全局代理：设置系统 SOCKS 代理，所有流量走本地代理
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def _serviceorder() -> list[str]:
    """按顺序返回启用的物理网络服务名（Wi-Fi、以太网等，跳过 VPN 虚拟服务）。"""
    try:
        r = subprocess.run(
            ["networksetup", "-listnetworkserviceorder"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return []
    services = []
    for ln in r.stdout.splitlines():
        ln = ln.strip()
        # 只匹配 "(1) Wi-Fi" 这类行，跳过 "(Hardware Port: ...)" 和空行
        m = re.match(r"^\(\d+\)\s+(.+)$", ln)
        if not m:
            continue
        name = m.group(1).strip()
        if not name:
            continue
        low = name.lower()
        if "shadowrocket" in low or "proton" in low or "vpn" in low or "utun" in low:
            continue
        services.append(name)
    return services


def _run(args: list[str]) -> None:
    try:
        subprocess.run(args, capture_output=True, timeout=10)
    except Exception:
        pass


CN_BYPASS = [
    "cn", "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn",
    "baidu.com", "baidustatic.com", "taobao.com", "tmall.com", "alicdn.com",
    "aliyun.com", "aliyuncs.com", "qq.com", "gtimg.com", "weixin.qq.com",
    "wechat.com", "douyin.com", "bytedance.com", "bilibili.com", "jd.com",
    "360buyimg.com", "163.com", "126.com", "sina.com.cn", "weibo.com",
    "zhihu.com", "csdn.net", "gitee.com", "mi.com", "xiaomi.com",
    "pinduoduo.com", "meituan.com", "dianping.com", "ctrip.com",
]


def set_global_proxy(port: int) -> None:
    """全局代理：所有流量走本地 SOCKS5（清空绕过列表）。"""
    for svc in _serviceorder():
        _run(["networksetup", "-setautoproxystate", svc, "off"])
        _run(["networksetup", "-setsocksfirewallproxy", svc, "127.0.0.1", str(port)])
        _run(["networksetup", "-setsocksfirewallproxystate", svc, "on"])
        _run(["networksetup", "-setproxybypassdomains", svc, ""])


def set_smart_proxy(port: int) -> None:
    """智能分流：全局 SOCKS 代理 + 国内域名绕过（直连）。

    macOS PAC 对 "SOCKS" 返回支持因浏览器而异（Safari 认 "SOCKS"、Chrome 认 "SOCKS5"），
    不可靠。改用「全局 SOCKS + 域名绕过列表」实现智能分流，跨浏览器一致。
    """
    for svc in _serviceorder():
        _run(["networksetup", "-setautoproxystate", svc, "off"])
        _run(["networksetup", "-setsocksfirewallproxy", svc, "127.0.0.1", str(port)])
        _run(["networksetup", "-setsocksfirewallproxystate", svc, "on"])
        _run(["networksetup", "-setproxybypassdomains", svc] + CN_BYPASS)


def set_pac_proxy(pac_path: str) -> None:
    """PAC 智能分流：设置系统自动代理 URL。（已弃用，改用 set_smart_proxy）"""
    url = f"file://{pac_path}"
    for svc in _serviceorder():
        _run(["networksetup", "-setautoproxyurl", svc, url])
        _run(["networksetup", "-setautoproxystate", svc, "on"])


def disable_proxy() -> None:
    """关闭系统代理（SOCKS + 自动代理 + 清空绕过列表）。"""
    for svc in _serviceorder():
        _run(["networksetup", "-setsocksfirewallproxystate", svc, "off"])
        _run(["networksetup", "-setautoproxystate", svc, "off"])
        _run(["networksetup", "-setproxybypassdomains", svc, ""])


def write_pac(port: int) -> str:
    """生成本地 PAC 文件（国内直连、国外走本地 SOCKS5），返回文件路径。"""
    pac = """function FindProxyForURL(url, host) {
    host = host.toLowerCase();
    // 回环 / 内网直连
    if (isPlainHostName(host) || host === "localhost") return "DIRECT";
    if (isInNet(host, "10.0.0.0", "255.0.0.0") ||
        isInNet(host, "172.16.0.0", "255.240.0.0") ||
        isInNet(host, "192.168.0.0", "255.255.0.0") ||
        isInNet(host, "127.0.0.0", "255.0.0.0") ||
        isInNet(host, "169.254.0.0", "255.255.0.0")) return "DIRECT";
    // 国内常见域名直连
    var cn = [
        "cn", "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn",
        "baidu.com", "baidustatic.com", "taobao.com", "tmall.com", "alicdn.com",
        "aliyun.com", "aliyuncs.com", "qq.com", "gtimg.com", "weixin.qq.com",
        "wechat.com", "douyin.com", "bytedance.com", "bilibili.com", "jd.com",
        "360buyimg.com", "163.com", "126.com", "sina.com.cn", "weibo.com",
        "zhihu.com", "csdn.net", "gitee.com", "mi.com", "xiaomi.com",
        "pinduoduo.com", "meituan.com", "dianping.com", "ctrip.com"
    ];
    for (var i = 0; i < cn.length; i++) {
        if (dnsDomainIs(host, cn[i]) || shExpMatch(host, "*." + cn[i])) return "DIRECT";
    }
    // 其余走本地 SOCKS5 代理（macOS PAC 里 "SOCKS" 即 SOCKS5，写 "SOCKS5" 不被识别）
    return "SOCKS 127.0.0.1:%d";
}
""" % port
    path = Path.home() / ".vultr-proxy-switcher" / "proxy.pac"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(pac, encoding="utf-8")
    return str(path)

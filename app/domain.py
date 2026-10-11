"""域名管理：子域名自动分配 + DNS 同步 + 服务器签证书（paramiko SSH，跨平台）。"""

from __future__ import annotations

from . import config
from .cloudflare import get_zone_id, sync_domain


def root_zone(domain: str) -> str:
    """从子域名反查主域名（zone）。jp2.yduas.cc → yduas.cc"""
    parts = domain.split(".")
    if len(parts) > 2:
        return ".".join(parts[1:])
    return domain


def assign_subdomain(server_id: str) -> str:
    """给服务器自动分配子域名 node{N}.主域名，并记录映射。"""
    domain = config.get("domain_name", "")
    zone = root_zone(domain)
    counter = int(config.get("domain_counter", 0)) + 1
    config.set_value("domain_counter", counter)
    sub = f"node{counter}.{zone}"
    sd = dict(config.get("server_domains", {}))
    sd[server_id] = sub
    config.set_value("server_domains", sd)
    return sub


def server_domain(server_id: str) -> str:
    """获取某台服务器的子域名（无则返回空）。"""
    return config.get("server_domains", {}).get(server_id, "")


def set_server_domain(server_id: str, subdomain: str) -> None:
    sd = dict(config.get("server_domains", {}))
    sd[server_id] = subdomain
    config.set_value("server_domains", sd)


def sync_server_dns(server_id: str, ip: str) -> str:
    """同步某台服务器的子域名 DNS 到新 IP。返回子域名。

    总是用 Cloudflare API 反查子域名所属的 zone（支持多一级域名）。
    """
    sub = server_domain(server_id)
    if not sub:
        return ""
    token = config.get("cf_token", "")
    if not token or not ip or ip in ("", "0.0.0.0"):
        return sub
    zid = get_zone_id(token, sub)  # 反查子域名所属的一级域名 zone
    sync_domain(token, zid, sub, ip)
    return sub


def ssh_exec(host: str, command: str, timeout: int = 180, password: str | None = None) -> tuple[str, str]:
    """paramiko SSH 执行（跨平台）。password 缺省用 config 的 root 密码。返回 (stdout, stderr)。"""
    import paramiko

    root_pw = password if password is not None else config.get("root_password", "")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username="root", password=root_pw, timeout=20, banner_timeout=20)
    try:
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode(errors="ignore")
        err = stderr.read().decode(errors="ignore")
        return out, err
    finally:
        client.close()


def sign_cert(server_ip: str, subdomain: str, password: str | None = None) -> tuple[str, str]:
    """SSH 到服务器，用 acme.sh（Let's Encrypt）签子域名证书并部署到 Hysteria2。

    用 Let's Encrypt（非 ZeroSSL）避免同一 IP 短时间多次申请触发限流。
    """
    zone = root_zone(subdomain)
    email = f"admin@{zone}"
    cmd = f"""set -e
if [ ! -f /root/.acme.sh/acme.sh ]; then
  for i in 1 2 3; do
    curl -s https://get.acme.sh | sh -s email={email} >/dev/null 2>&1 && break
    sleep 5
  done
fi
ufw allow 80/tcp 2>/dev/null || true
/root/.acme.sh/acme.sh --issue -d {subdomain} --standalone --keylength ec-256 --server letsencrypt 2>&1 | tail -3
/root/.acme.sh/acme.sh --install-cert -d {subdomain} --ecc \
  --key-file /etc/hysteria/server.key \
  --fullchain-file /etc/hysteria/server.crt \
  --reloadcmd 'systemctl restart hysteria' 2>&1 | tail -2
systemctl restart hysteria
echo DONE
"""
    out, err = ssh_exec(server_ip, cmd, timeout=300, password=password)
    if "DONE" not in out:
        raise RuntimeError(f"签证书失败：{out.strip()[-300:]} {err.strip()[-200:]}")
    return out, err


def apply_server_config(server_ip: str, creds: dict, ssh_password: str | None = None) -> tuple[str, str]:
    """SSH 热更新服务器配置（不换 IP）：改 root 密码 + gost + xray + hysteria，重启服务。

    用于「保存并重启」——修改账号/密码/端口后直接应用到已在运行的服务器，IP 不变。
    """
    import base64 as _b64
    import json as _json

    proxy_user = creds.get("proxy_user", "")
    proxy_pass = creds.get("proxy_pass", "")
    proxy_port = int(creds.get("proxy_port", 1080))
    ss_pass = creds.get("ss_password", "")
    ss_port = int(creds.get("ss_port", 45678))
    root_pass = creds.get("root_password", "")
    ruuid = creds.get("reality_uuid", "")
    rpriv = creds.get("reality_private_key", "")
    rshort = creds.get("reality_short_id", "")
    rsni = creds.get("reality_sni", "www.cloudflare.com")
    rport = int(creds.get("reality_port", 443))
    hy_pass = creds.get("hysteria_password", "")
    hy_port = int(creds.get("hysteria_port", 8443))
    hy_mport = creds.get("hysteria_mport", "20000-20100")
    hop_ports = hy_mport.replace("-", ":") if hy_mport else ""

    listeners = f"-L socks5://{proxy_user}:{proxy_pass}@:{proxy_port}"
    if ss_pass:
        listeners += f" -L ss://aes-256-gcm:{ss_pass}@:{ss_port}"

    xray_cfg = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "listen": "0.0.0.0",
                "port": rport,
                "protocol": "vless",
                "settings": {"clients": [{"id": ruuid, "flow": "xtls-rprx-vision"}], "decryption": "none"},
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "dest": f"{rsni}:443",
                        "xver": 0,
                        "serverNames": [rsni],
                        "privateKey": rpriv,
                        "shortIds": [rshort],
                    },
                },
                "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
            }
        ],
        "outbounds": [
            {"protocol": "freedom", "tag": "direct"},
            {"protocol": "blackhole", "tag": "blocked"},
        ],
    }
    xray_b64 = _b64.b64encode(_json.dumps(xray_cfg).encode()).decode()

    parts = [
        "cat > /etc/systemd/system/gost.service <<'GEOF'\n"
        "[Unit]\nDescription=gost proxy (socks5 + shadowsocks)\nAfter=network.target\n"
        "[Service]\n"
        f"ExecStart=/usr/local/bin/gost {listeners}\n"
        "Restart=always\nRestartSec=3\nLimitNOFILE=65535\n"
        "[Install]\nWantedBy=multi-user.target\nGEOF\n"
        "systemctl daemon-reload\nsystemctl restart gost",
    ]
    if ruuid and rpriv:
        parts.append(
            f"mkdir -p /usr/local/etc/xray\necho '{xray_b64}' | base64 -d > /usr/local/etc/xray/config.json\n"
            "systemctl restart xray"
        )
    if hy_pass:
        hop_rule = (
            f"iptables -t nat -C PREROUTING -p udp --dport {hop_ports} -j REDIRECT --to-ports {hy_port} "
            f"2>/dev/null || iptables -t nat -A PREROUTING -p udp --dport {hop_ports} -j REDIRECT --to-ports {hy_port}"
        )
        parts.append(
            "cat > /etc/hysteria/config.yaml <<'HYEOF'\n"
            f"listen: :{hy_port}\ntls:\n  cert: /etc/hysteria/server.crt\n  key: /etc/hysteria/server.key\n"
            "auth:\n  type: password\n"
            f"  password: {hy_pass}\nHYEOF\n"
            f"{hop_rule}\nsystemctl restart hysteria"
        )
    if root_pass:
        parts.append(f"echo 'root:{root_pass}' | chpasswd")
    parts.append("echo APPLY_DONE")
    script = "\n".join(parts)
    out, err = ssh_exec(server_ip, script, timeout=180, password=ssh_password)
    if "APPLY_DONE" not in out:
        raise RuntimeError(f"应用配置失败：{out.strip()[-300:]} {err.strip()[-200:]}")
    return out, err


def restart_services(server_ip: str, ssh_password: str | None = None) -> tuple[str, str]:
    """单纯重启服务器上的 gost/xray/hysteria 服务（不换 IP，不改配置）。"""
    cmd = "systemctl restart gost xray hysteria 2>/dev/null\nsystemctl is-active gost xray hysteria\n"
    return ssh_exec(server_ip, cmd, timeout=120, password=ssh_password)


def resolve_domain(domain: str) -> list[str]:
    """解析域名到 A 记录 IP 列表（用系统 DNS resolver，标准库）。"""
    import socket
    try:
        infos = socket.getaddrinfo(domain, None, socket.AF_INET)
        return sorted(set(x[4][0] for x in infos))
    except Exception:
        return []


def is_fake_ip(ip: str) -> bool:
    """判断 IP 是否是 fake-ip（VPN/代理的 DNS 劫持地址）。

    Shadowrocket/Clash 默认 fake-ip 网段 198.18.0.0/16，Surge 用 28.0.0.0/8。
    解析到这些网段说明系统 DNS 被 VPN 隧道劫持，结果不可信。
    """
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        a = int(parts[0])
        b = int(parts[1])
    except ValueError:
        return False
    if a == 198 and b == 18:
        return True
    if a == 28:
        return True
    return False

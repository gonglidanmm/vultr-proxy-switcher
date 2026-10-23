"""Vultr API 客户端 + gost 安装脚本。"""

from __future__ import annotations

import base64
import socket

import requests

# 强制 requests 走 IPv4：用户的 Vultr allowlist 只放行了 IPv4，而 macOS 残留的
# utun 隧道会让 requests 优先走 IPv6（被 Vultr 拒）。强制 AF_INET 规避。
try:
    from urllib3.util import connection as _conn

    _conn.allowed_gai_family = lambda: socket.AF_INET
except Exception:
    pass

BASE = "https://api.vultr.com/v2"

# 套餐 -> 显示名（价格 + 流量 + 特性，下拉框/列表统一用这个辨认）
PLAN_NAMES = {
    "vc2-1c-1gb": "$5/月 · 1TB流量 · 常规1核1G",
    "vhf-1c-1gb": "$6/月 · 1TB流量 · 高频3.7GHz",
    "vhp-1c-1gb-amd": "$6/月 · 2TB流量 · AMD EPYC",
    "vhp-1c-1gb-intel": "$6/月 · 2TB流量 · Intel高频",
    "vc2-1c-0.5gb-v6": "$2.5/月 · 512GB流量 · IPv6-only",
}

# 套餐月价（美元/月），用于「剩余可分配天数」预算
PLAN_MONTHLY_PRICE = {
    "vc2-1c-1gb": 5.0,
    "vhf-1c-1gb": 6.0,
    "vhp-1c-1gb-amd": 6.0,
    "vhp-1c-1gb-intel": 6.0,
    "vc2-1c-0.5gb-v6": 2.5,
}

# Ubuntu 24.04 的 os_id（旧脚本用 1743 = Ubuntu 22.04；24.04 为 2284）
OS_ID = 1743

# 区域 → 国家分组（切换IP时同国家换区）
# 注意：Vultr region id 会更新，废弃的（sjo/osa/yul/tor）已替换为新 id（itm/yto）
REGION_COUNTRY = {
    # 美国
    "atl": "us", "ord": "us", "dfw": "us", "ewr": "us", "lax": "us",
    "mia": "us", "sea": "us", "sjc": "us",
    # 日本
    "nrt": "jp", "itm": "jp",
    # 韩国 / 新加坡
    "icn": "kr", "sgp": "sg",
    # 英国 / 德国 / 荷兰 / 法国
    "lhr": "gb", "man": "gb", "fra": "de", "ams": "nl", "cdg": "fr",
    # 加拿大 / 澳大利亚 / 印度
    "yto": "ca", "syd": "au", "mel": "au", "bom": "in", "del": "in",
}

COUNTRY_REGIONS = {
    "us": ["atl", "ord", "dfw", "ewr", "lax", "mia", "sea", "sjc"],
    "jp": ["nrt", "itm"],
    "kr": ["icn"],
    "sg": ["sgp"],
    "gb": ["lhr", "man"],
    "de": ["fra"],
    "nl": ["ams"],
    "fr": ["cdg"],
    "ca": ["yto"],
    "au": ["syd", "mel"],
    "in": ["bom", "del"],
}


def regions_for(region: str) -> list[str]:
    """根据当前区域返回同一国家的可选区域列表（未知区域退回同区域）。"""
    country = REGION_COUNTRY.get(region)
    if country and country in COUNTRY_REGIONS:
        return COUNTRY_REGIONS[country]
    return [region]


COUNTRY_NAMES = {
    "us": "美国", "jp": "日本", "kr": "韩国", "sg": "新加坡",
    "gb": "英国", "de": "德国", "nl": "荷兰", "fr": "法国",
    "ca": "加拿大", "au": "澳大利亚", "in": "印度",
}


def country_of(region: str) -> str | None:
    """返回区域所属的国家代码（未知返回 None）。"""
    return REGION_COUNTRY.get(region)


# 区域 → 城市名（切换IP弹窗显示用）
REGION_CITY = {
    "atl": "亚特兰大", "ord": "芝加哥", "dfw": "达拉斯", "ewr": "纽约",
    "lax": "洛杉矶", "mia": "迈阿密", "sea": "西雅图", "sjc": "圣何塞",
    "nrt": "东京", "itm": "大阪", "icn": "首尔", "sgp": "新加坡",
    "lhr": "伦敦", "man": "曼彻斯特", "fra": "法兰克福", "ams": "阿姆斯特丹",
    "cdg": "巴黎", "yto": "多伦多", "syd": "悉尼", "mel": "墨尔本",
    "bom": "孟买", "del": "德里",
}

# 区域 → 中国大陆直连速度评级（快/中/慢）
REGION_SPEED = {
    # 快：离中国大陆近 / 线路好
    "lax": "快", "sjc": "快", "sea": "快",
    "nrt": "快", "itm": "快", "icn": "快", "sgp": "快",
    # 中：美国中部
    "dfw": "中", "ord": "中",
    # 慢：离中国大陆远（东海岸/欧洲/澳洲）
    "atl": "慢", "mia": "慢", "ewr": "慢",
    "lhr": "慢", "man": "慢", "fra": "慢", "ams": "慢", "cdg": "慢",
    "yto": "慢", "syd": "慢", "mel": "慢", "bom": "慢", "del": "慢",
}


def country_name(code: str) -> str:
    """国家代码 → 中文名。"""
    return COUNTRY_NAMES.get(code, code)


def region_label(region: str) -> str:
    """区域代码 → '国家·城市'（如 atl → '美国·亚特兰大'）。未知返回原代码。"""
    country = REGION_COUNTRY.get(region)
    city = REGION_CITY.get(region, region)
    if country:
        return f"{COUNTRY_NAMES.get(country, country)}·{city}"
    return city


def build_gost_user_data(user: str, password: str, port: int, creds: dict | None = None) -> str:
    """cloud-init：装 gost（SOCKS5+SS）+ Xray（VLESS+Reality），设 root 密码。

    creds 为该服务器的独立凭据 dict（per-server），缺省时回退全局 config。
    """
    import json as _json

    from . import config as _cfg

    c = creds or {}
    ss_password = c.get("ss_password", _cfg.get("ss_password", ""))
    ss_port = int(c.get("ss_port", _cfg.get("ss_port", 45678)))
    root_password = c.get("root_password", _cfg.get("root_password", ""))
    reality_uuid = c.get("reality_uuid", _cfg.get("reality_uuid", ""))
    reality_private_key = c.get("reality_private_key", _cfg.get("reality_private_key", ""))
    reality_short_id = c.get("reality_short_id", _cfg.get("reality_short_id", ""))
    reality_sni = c.get("reality_sni", _cfg.get("reality_sni", "www.microsoft.com"))
    reality_port = int(c.get("reality_port", _cfg.get("reality_port", 443)))
    hysteria_password = c.get("hysteria_password", _cfg.get("hysteria_password", ""))
    hysteria_port = int(c.get("hysteria_port", _cfg.get("hysteria_port", 8443)))
    hysteria_mport = c.get("hysteria_mport", _cfg.get("hysteria_mport", "20000-20100"))
    hop_ports = hysteria_mport.replace("-", ":") if hysteria_mport else ""

    listeners = f"-L socks5://{user}:{password}@:{port}"
    if ss_password:
        listeners += f" -L ss://aes-256-gcm:{ss_password}@:{ss_port}"
    root_line = f"echo 'root:{root_password}' | chpasswd" if root_password else ""

    xray_section = ""
    if reality_uuid and reality_private_key:
        cfg = _json.dumps(
            {
                "log": {"loglevel": "warning"},
                "inbounds": [
                    {
                        "listen": "0.0.0.0",
                        "port": reality_port,
                        "protocol": "vless",
                        "settings": {
                            "clients": [{"id": reality_uuid, "flow": "xtls-rprx-vision"}],
                            "decryption": "none",
                        },
                        "streamSettings": {
                            "network": "tcp",
                            "security": "reality",
                            "realitySettings": {
                                "dest": f"{reality_sni}:443",
                                "xver": 0,
                                "serverNames": [reality_sni],
                                "privateKey": reality_private_key,
                                "shortIds": [reality_short_id],
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
        )
        cfg_b64 = base64.b64encode(cfg.encode()).decode()
        xray_section = f"""
# Xray (VLESS+Reality)
if [ ! -x /usr/local/bin/xray ]; then
  echo "下载 xray..."
  wget -q --timeout=60 --tries=3 -O /tmp/xray.zip https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip || echo "xray 下载失败"
  unzip -q -o /tmp/xray.zip -d /usr/local/bin/ 2>/dev/null || true
  chmod +x /usr/local/bin/xray 2>/dev/null || true
fi
mkdir -p /usr/local/etc/xray
echo '{cfg_b64}' | base64 -d > /usr/local/etc/xray/config.json
cat > /etc/systemd/system/xray.service <<'XEOF'
[Unit]
Description=Xray Service
After=network.target
[Service]
ExecStart=/usr/local/bin/xray run -config /usr/local/etc/xray/config.json
Restart=on-failure
[Install]
WantedBy=multi-user.target
XEOF
systemctl daemon-reload
systemctl enable xray 2>/dev/null || true
systemctl restart xray || echo "xray 启动失败"
"""

    hysteria_section = ""
    ufw_hysteria = ""
    if hysteria_password:
        ufw_hysteria = (
            f"ufw allow {hysteria_port}/udp 2>/dev/null || true\n"
            f"ufw allow {hop_ports}/udp 2>/dev/null || true\n"
        )
    if hysteria_password:
        hop_rule = (
            f"iptables -t nat -C PREROUTING -p udp --dport {hop_ports} -j REDIRECT --to-ports {hysteria_port} "
            f"2>/dev/null || iptables -t nat -A PREROUTING -p udp --dport {hop_ports} -j REDIRECT --to-ports {hysteria_port}"
        )
        hysteria_section = f"""
# Hysteria2
if [ ! -x /usr/local/bin/hysteria ]; then
  echo "下载 hysteria..."
  wget -q --timeout=60 --tries=3 -O /usr/local/bin/hysteria https://github.com/apernet/hysteria/releases/download/app/v2.6.1/hysteria-linux-amd64 || echo "hysteria 下载失败"
  chmod +x /usr/local/bin/hysteria 2>/dev/null || true
fi
mkdir -p /etc/hysteria
openssl req -x509 -nodes -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -keyout /etc/hysteria/server.key -out /etc/hysteria/server.crt -days 3650 -subj '/CN=hysteria' 2>/dev/null
cat > /etc/hysteria/config.yaml <<HYEOF
listen: :{hysteria_port}
tls:
  cert: /etc/hysteria/server.crt
  key: /etc/hysteria/server.key
auth:
  type: password
  password: {hysteria_password}
HYEOF
cat > /etc/systemd/system/hysteria.service <<'HYSEOF'
[Unit]
Description=Hysteria2
After=network.target
[Service]
ExecStartPre=/bin/sh -c '{hop_rule}'
ExecStart=/usr/local/bin/hysteria server -c /etc/hysteria/config.yaml
Restart=always
RestartSec=3
LimitNOFILE=65535
[Install]
WantedBy=multi-user.target
HYSEOF
systemctl daemon-reload
systemctl enable hysteria 2>/dev/null || true
systemctl restart hysteria || echo "hysteria 启动失败"
"""

    script = f"""#!/bin/bash
# 不用 set -e：任何一步失败都不能中断整个初始化，否则后续服务都不装、实例变"半死"
echo "=== cloud-init start: gost+xray+hysteria ==="
apt-get update -y >/dev/null 2>&1 || echo "apt update 失败(忽略)"
{root_line}
ufw allow {port}/tcp 2>/dev/null || true
ufw allow {port}/udp 2>/dev/null || true
ufw allow {ss_port}/tcp 2>/dev/null || true
ufw allow {ss_port}/udp 2>/dev/null || true
ufw allow {reality_port}/tcp 2>/dev/null || true
{ufw_hysteria}ufw allow 22/tcp 2>/dev/null || true
systemctl enable --now ssh 2>/dev/null || systemctl enable --now sshd 2>/dev/null || true
ufw --force enable 2>/dev/null || true
systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || true
if [ ! -x /usr/local/bin/gost ]; then
  echo "下载 gost..."
  wget -q --timeout=60 --tries=3 -O /usr/local/bin/gost.gz https://github.com/ginuerzh/gost/releases/download/v2.11.5/gost-linux-amd64-2.11.5.gz || echo "gost 下载失败"
  gunzip -f /usr/local/bin/gost.gz 2>/dev/null || true
  chmod +x /usr/local/bin/gost 2>/dev/null || true
fi
cat > /etc/systemd/system/gost.service <<'GEOF'
[Unit]
Description=gost proxy (socks5 + shadowsocks)
After=network.target
[Service]
ExecStart=/usr/local/bin/gost {listeners}
Restart=always
RestartSec=3
LimitNOFILE=65535
[Install]
WantedBy=multi-user.target
GEOF
systemctl daemon-reload
systemctl enable gost 2>/dev/null || true
systemctl restart gost || echo "gost 启动失败"
{xray_section}
{hysteria_section}
echo "=== cloud-init done ==="
"""
    return base64.b64encode(script.encode()).decode()


class VultrError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class VultrAPI:
    def __init__(self, api_key: str):
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._session = requests.Session()
        self._session.headers.update(self.headers)

    def _req(self, method: str, path: str, **kwargs) -> dict:
        # 强制直连，避免被系统代理（如 v2rayN/Shadowrocket 的本地代理端口）劫持导致失败
        kwargs.setdefault("proxies", {"http": None, "https": None})
        last_err: requests.RequestException | None = None
        # 最多重试 2 次：连接池里的 keep-alive 死连接会导致假超时，重建连接池即可恢复
        for _attempt in range(3):
            try:
                r = self._session.request(method, BASE + path, timeout=(15, 60), **kwargs)
                break
            except requests.RequestException as e:
                last_err = e
                try:
                    self._session.close()
                except Exception:
                    pass
                self._session = requests.Session()
                self._session.headers.update(self.headers)
        else:
            raise VultrError(f"网络错误: {last_err}") from last_err
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400:
            err = data.get("error", f"HTTP {r.status_code}")
            raise VultrError(err, r.status_code)
        return data

    def list_instances(self) -> list[dict]:
        return self._req("GET", "/instances").get("instances", [])

    def get_instance(self, iid: str) -> dict:
        return self._req("GET", f"/instances/{iid}").get("instance", {})

    def create_instance(self, region: str, plan: str, label: str, user_data_b64: str) -> dict:
        payload = {
            "region": region,
            "plan": plan,
            "os_id": OS_ID,
            "label": label,
            "user_data": user_data_b64,
            "backups": "disabled",
        }
        data = self._req("POST", "/instances", json=payload)
        return data.get("instance", {})

    def delete_instance(self, iid: str) -> None:
        self._req("DELETE", f"/instances/{iid}")

    def get_account(self) -> dict:
        return self._req("GET", "/account").get("account", {})

    def get_plans(self) -> list[dict]:
        return self._req("GET", "/plans").get("plans", [])

    def get_instance_bandwidth(self, iid: str) -> dict:
        """返回实例的每日带宽 {date: {incoming_bytes, outgoing_bytes}}。"""
        return self._req("GET", f"/instances/{iid}/bandwidth").get("bandwidth", {})

    def get_account_bandwidth(self) -> dict:
        """返回账户级带宽信息（免费/实例/购买的带宽配额 + 已用 + 超额）。"""
        return self._req("GET", "/account/bandwidth").get("bandwidth", {})

    def halt_instance(self, iid: str) -> None:
        self._req("POST", f"/instances/{iid}/halt")

    def start_instance(self, iid: str) -> None:
        self._req("POST", f"/instances/{iid}/start")



def compute_billing(instances: list[dict], plans: list[dict], account: dict) -> dict:
    """计算计费状态。

    Vultr 记账约定：balance 负号 = 预付余额（credit），正号 = 欠费。
    返回余额、本月已用、剩余可用、每小时扣费、剩余可用天数。
    """
    balance = float(account.get("balance", 0) or 0)
    pending = float(account.get("pending_charges", 0) or 0)
    hourly_map = {p.get("id"): float(p.get("hourly_cost", 0) or 0) for p in plans}
    running = [i for i in instances if i.get("power_status") == "running"]
    total_hourly = sum(hourly_map.get(i.get("plan", ""), 0) for i in running)
    credit = -balance if balance < 0 else 0  # 预付余额（正数）
    owed = balance if balance > 0 else 0  # 欠费（正数）
    remaining = credit - pending  # 剩余可用（可能为负 = 快用完）
    days_left = None
    if total_hourly > 0 and remaining > 0:
        days_left = (remaining / total_hourly) / 24
    return {
        "balance": balance,
        "pending": pending,
        "credit": credit,
        "owed": owed,
        "remaining": remaining,
        "running_count": len(running),
        "total_hourly": total_hourly,
        "days_left": days_left,
    }


def compute_bandwidth(instances: list[dict], plans: list[dict], api) -> dict:
    """计算每台运行中服务器的已用流量（出站）和剩余流量。

    Vultr 只对出站（outgoing）流量计费，入站免费。
    已用 = 该服务器（instance_id）的历史累计（切换 IP 时随服务器迁移）+ 当前实例出站。
    返回 {iid: {used_gb, quota_gb, remaining_gb}}。

    性能：100-200 台时串行查询要 1-3 分钟，这里用线程池并行查询（每个线程独立 VultrAPI/Session，
    避免 requests.Session 线程不安全），缩短到 10-20 秒。
    """
    from concurrent.futures import ThreadPoolExecutor

    from . import config

    plan_bw = {p.get("id"): int(p.get("bandwidth", 0) or 0) for p in plans}
    cumulative = dict(config.get("server_cumulative_used", {}) or {})
    flow_reset = dict(config.get("server_flow_reset", {}) or {})
    running = [i for i in instances if i.get("power_status") == "running" and i.get("id")]
    if not running:
        return {}

    # 从 api 实例拿 api_key，供每线程新建独立 VultrAPI（独立 Session）
    api_key = (api.headers.get("Authorization", "") or "").replace("Bearer ", "").strip()
    plan_by_id = {i.get("id"): i.get("plan", "") for i in running}

    def _fetch(iid):
        # 每线程独立 VultrAPI，避免共享 Session 的线程安全问题
        try:
            a = VultrAPI(api_key)
            return iid, a.get_instance_bandwidth(iid)
        except Exception:
            return iid, {}

    result = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for iid, bw in pool.map(_fetch, [i["id"] for i in running]):
            total_out = sum(day.get("outgoing_bytes", 0) for day in bw.values())
            used_gb = total_out / 1e9
            # 累计已用 = 该服务器历史累计（切换 IP 时随服务器迁移）+ 当前实例出站
            # 减去重置基准：重置流量后从 0 重新算（否则 sync_flow 会用当前实例出站覆盖，重置失效）
            cum = float(cumulative.get(iid, 0) or 0)
            reset_base = float(flow_reset.get(iid, 0) or 0)
            total_used = cum + max(0.0, used_gb - reset_base)
            quota = plan_bw.get(plan_by_id.get(iid, ""), 0)
            result[iid] = {
                "used_gb": total_used,
                "quota_gb": quota,
                "remaining_gb": quota - total_used,
            }
    return result


def compute_account_bandwidth(account_bw: dict) -> dict:
    """解析账户级带宽（Vultr 官方口径：账户级，非每实例独立）。

    Vultr 带宽上限 = 免费带宽(每月 2TB) + 实例带宽(按运行小时累计) + 购买的带宽。
    已用 = 出站流量(gb_out)；超额 = 超出配额的部分($0.01/GB)。
    返回 {total_gb, free_gb, instance_gb, used_gb, remaining_gb, overage_gb, overage_cost}。
    """
    cur = account_bw.get("current_month_to_date", {}) or {}
    free = float(cur.get("free_bandwidth_credits", 0) or 0)
    inst = float(cur.get("instance_bandwidth_credits", 0) or 0)
    purchased = float(cur.get("purchased_bandwidth_credits", 0) or 0)
    total = free + inst + purchased
    used = float(cur.get("gb_out", 0) or 0)
    overage = float(cur.get("overage", 0) or 0)
    overage_cost = float(cur.get("overage_cost", 0) or 0)
    return {
        "total_gb": total,
        "free_gb": free,
        "instance_gb": inst,
        "used_gb": used,
        "remaining_gb": total - used,
        "overage_gb": overage,
        "overage_cost": overage_cost,
    }


#!/usr/bin/env python3
"""前置服务器切换/停服/计量接口。

部署：scp 到服务器 /usr/local/bin/switch_server.py + systemd 托管
调用（均需 X-Api-Key 头匹配 /etc/switch_api_key）：
  POST /switch  {"relay_id","host","port","user","password"}  改上游 webshare IP + 重启单服务（切 IP）
  POST /stop    {"relay_id"}                                  停服务（回收用：disable --now，防重启自起）
  GET  /traffic                                               读 per-relay 流量（iptables 按端口计数）

原理：
- SS 入站走 gost（每 relay 一个 gost-relay-<id>.service）；Reality/Hysteria2 走 sing-box（每 relay 一个 sing-box-relay-<id>.service）。
- 切换只改单 relay 的 -F 上游 + 重启单服务，不影响其它 relay。
- 流量计量：iptables 记账规则（INPUT+OUTPUT，relay 的 SS/SOCKS5/Reality/Hysteria2 四端口），
  只计数不改流（跳 RELAY_TRAFFIC 链后 RETURN，落回 ufw 正常处理）。
"""

from __future__ import annotations

import glob
import json
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GOST_DIR = "/etc/systemd/system"
SINGBOX_CFG = "/etc/sing-box/config.json"
API_KEY_FILE = "/etc/switch_api_key"
SOCKS_OFFSET = 10000
REALITY_OFFSET = 8000
HYSTERIA_OFFSET = 12000
TRAFFIC_CHAIN = "RELAY_TRAFFIC"


def read_api_key() -> str:
    try:
        with open(API_KEY_FILE) as f:
            return f.read().strip()
    except Exception:
        return ""


def _run(cmd: list[str]) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def gost_exec(relay_password: str, relay_port: int, host: str, port: int, user: str, password: str) -> str:
    sp = relay_port + SOCKS_OFFSET
    return (
        f"/usr/local/bin/gost "
        f"-L ss://aes-256-gcm:{relay_password}@:{relay_port} "
        f"-L socks5://proxy:{relay_password}@:{sp} "
        f"-F socks5://{user}:{password}@{host}:{port}"
    )


def switch_gost(relay_id: str, host: str, port: int, user: str, password: str) -> dict:
    """改 gost-relay-<id>.service 的 -F 上游并重启该服务（不动其它 relay）。"""
    unit_path = f"{GOST_DIR}/gost-relay-{relay_id}.service"
    try:
        with open(unit_path) as f:
            old = f.read()
    except FileNotFoundError:
        return {"ok": False, "error": f"找不到服务 gost-relay-{relay_id}"}

    # 提取现有 relay_password / relay_port（切换只改上游，这两项不变，客户密码不失效）
    m = re.search(r"ss://aes-256-gcm:([0-9a-f]+)@:(\d+)", old)
    if not m:
        return {"ok": False, "error": "解析 gost ExecStart 失败"}
    relay_password, relay_port = m.group(1), int(m.group(2))

    new_unit = f"""[Unit]
Description=gost relay {relay_id}
After=network.target
[Service]
ExecStart={gost_exec(relay_password, relay_port, host, port, user, password)}
Restart=always
RestartSec=3
LimitNOFILE=65535
[Install]
WantedBy=multi-user.target
"""

    with open(unit_path, "w") as f:
        f.write(new_unit)
    _run(["systemctl", "daemon-reload"])
    rc, err = _run(["systemctl", "restart", f"gost-relay-{relay_id}"])
    if rc != 0:
        return {"ok": False, "error": f"gost 重启失败: {err.strip()}"}
    return {"ok": True, "relay_port": relay_port}


def switch_singbox(relay_id: str, host: str, port: int, user: str, password: str) -> dict:
    """改 /etc/sing-box/relay-<id>.json 的 outbound 上游并重启 sing-box-relay-<id>（单进程，不影响其他 relay）。"""
    cfg_path = f"/etc/sing-box/relay-{relay_id}.json"
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return {"ok": False, "error": f"读 {cfg_path} 失败: {e}"}

    found = False
    for ob in cfg.get("outbounds", []):
        if ob.get("tag") == "out":
            ob["server"] = host
            ob["server_port"] = int(port)
            ob["username"] = user
            ob["password"] = password
            found = True
            break
    if not found:
        return {"ok": False, "error": f"{cfg_path} 里找不到 outbound 'out'"}

    with open(cfg_path, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    rc, err = _run(["systemctl", "restart", f"sing-box-relay-{relay_id}"])
    if rc != 0:
        return {"ok": False, "error": f"sing-box-relay-{relay_id} 重启失败: {err.strip()}"}
    return {"ok": True, "relay_port": None}


# ---------- 停服务（回收） ----------

def stop_relay(relay_id: str) -> dict:
    """回收 = 停服务：disable --now（停止 + 防止服务器重启后自起）。幂等，服务不存在也算成功。"""
    ok_all = True
    errors = []
    for svc in (f"gost-relay-{relay_id}", f"sing-box-relay-{relay_id}"):
        rc, err = _run(["systemctl", "disable", "--now", svc])
        # 服务已停/不存在 = 成功（幂等）；真失败才记
        if rc != 0 and "does not exist" not in err and "not loaded" not in err:
            ok_all = False
            errors.append(f"{svc}: {err.strip()}")
    return {"ok": ok_all, "error": "; ".join(errors)}


# ---------- 流量计量（iptables 按端口计数） ----------

def discover_relays() -> dict[str, int]:
    """从运行中的 gost-relay-* 服务发现 relay，返回 {relay_id: relay_port}。

    从 unit 文件的 ExecStart 正则提取 relay_port（SS 入站端口），其余端口由偏移推导。
    """
    relays: dict[str, int] = {}
    for unit in glob.glob(f"{GOST_DIR}/gost-relay-*.service"):
        rid = unit.rsplit("/", 1)[-1].replace("gost-relay-", "").replace(".service", "")
        try:
            with open(unit) as f:
                content = f.read()
        except Exception:
            continue
        m = re.search(r"ss://aes-256-gcm:[0-9a-f]+@:(\d+)", content)
        if not m:
            continue
        relays[rid] = int(m.group(1))
    return relays


def _ensure_chain() -> None:
    """确保记账链 RELAY_TRAFFIC 存在且末尾 RETURN（幂等）。"""
    _run(["iptables", "-N", TRAFFIC_CHAIN])
    rc, _ = _run(["iptables", "-C", TRAFFIC_CHAIN, "-j", "RETURN"])
    if rc != 0:
        _run(["iptables", "-A", TRAFFIC_CHAIN, "-j", "RETURN"])


def _ensure_rule(chain: str, proto: str, port_match: list[str], relay_id: str) -> None:
    """确保某条记账规则存在（幂等）。-I 插到链顶，记账链 RETURN 不改流。"""
    full = ["-p", proto] + port_match + ["-m", "comment", "--comment", f"relay-{relay_id}", "-j", TRAFFIC_CHAIN]
    rc, _ = _run(["iptables", "-C", chain] + full)
    if rc != 0:
        _run(["iptables", "-I", chain, "1"] + full)


def ensure_traffic_rules(relays: dict[str, int]) -> None:
    """为每个 relay 的 4 个端口（SS/SOCKS5/Reality/Hysteria2）建立 INPUT+OUTPUT 记账规则。"""
    _ensure_chain()
    for rid, rp in relays.items():
        ss, socks, reality, hy = rp, rp + SOCKS_OFFSET, rp + REALITY_OFFSET, rp + HYSTERIA_OFFSET
        # INPUT：客户 → relay（上传）
        _ensure_rule("INPUT", "tcp", ["-m", "multiport", "--dports", f"{ss},{socks},{reality}"], rid)
        _ensure_rule("INPUT", "udp", ["--dport", str(hy)], rid)
        # OUTPUT：relay → 客户（下载）
        _ensure_rule("OUTPUT", "tcp", ["-m", "multiport", "--sports", f"{ss},{socks},{reality}"], rid)
        _ensure_rule("OUTPUT", "udp", ["--sport", str(hy)], rid)


def read_traffic(relays: dict[str, int]) -> dict[str, dict[str, int]]:
    """读 INPUT/OUTPUT 链记账规则的字节计数，返回 {relay_id: {in_bytes, out_bytes}}（原始计数，未做累加/重置处理）。"""
    result: dict[str, dict[str, int]] = {}
    for chain in ("INPUT", "OUTPUT"):
        _, out = _run(["iptables", "-L", chain, "-v", "-x", "-n"])
        for line in out.splitlines():
            for rid in relays:
                if f"relay-{rid}" in line:
                    parts = line.split()
                    # 列：pkts bytes target prot opt in out source destination ...
                    if len(parts) >= 2:
                        try:
                            result.setdefault(rid, {"in": 0, "out": 0})
                            if chain == "INPUT":
                                result[rid]["in"] += int(parts[1])
                            else:
                                result[rid]["out"] += int(parts[1])
                        except ValueError:
                            pass
    return result


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, obj: dict) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())

    def _auth_ok(self) -> bool:
        key = self.headers.get("X-Api-Key", "")
        return bool(key) and key == read_api_key()

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return {}

    def do_GET(self) -> None:
        if self.path != "/traffic":
            return self._json(404, {"ok": False, "error": "not found"})
        if not self._auth_ok():
            return self._json(401, {"ok": False, "error": "unauthorized"})
        try:
            relays = discover_relays()
            ensure_traffic_rules(relays)
            traffic = read_traffic(relays)
            return self._json(200, {"ok": True, "traffic": traffic})
        except Exception as e:
            return self._json(500, {"ok": False, "error": str(e)})

    def do_POST(self) -> None:
        if self.path not in ("/switch", "/stop"):
            return self._json(404, {"ok": False, "error": "not found"})

        if not self._auth_ok():
            return self._json(401, {"ok": False, "error": "unauthorized"})

        body = self._body()
        relay_id = str(body.get("relay_id", ""))

        if self.path == "/stop":
            if not relay_id:
                return self._json(400, {"ok": False, "error": "缺少参数 relay_id"})
            return self._json(200, stop_relay(relay_id))

        # /switch
        host = str(body.get("host", ""))
        port = int(body.get("port", 0) or 0)
        user = str(body.get("user", ""))
        password = str(body.get("password", ""))
        if not (relay_id and host and port and user and password):
            return self._json(400, {"ok": False, "error": "缺少参数 relay_id/host/port/user/password"})

        gost_res = switch_gost(relay_id, host, port, user, password)
        sb_res = switch_singbox(relay_id, host, port, user, password)

        ok = gost_res.get("ok") and sb_res.get("ok")
        err = ""
        if not gost_res.get("ok"):
            err = f"gost: {gost_res.get('error')}"
        if not sb_res.get("ok"):
            err = (err + "; " if err else "") + f"sing-box: {sb_res.get('error')}"
        self._json(200, {"ok": ok, "error": err, "relay_port": gost_res.get("relay_port")})

    def log_message(self, *args) -> None:
        pass  # 静默，避免刷 systemd 日志


def main() -> None:
    port = 8080
    # 监听公网，Worker（Cloudflare）通过服务器公网 IP 调用；X-Api-Key 认证保护
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()

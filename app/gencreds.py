"""每台服务器的独立凭据生成 + 读取（per-server 优先，fallback 全局默认）。"""

from __future__ import annotations

import secrets
import string
import subprocess
import uuid as _uuid
from pathlib import Path

from . import config

_BIN_DIR = Path(__file__).resolve().parent.parent / "bin"


def _rand(n: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def _x25519_keypair() -> tuple[str, str]:
    """用 xray x25519 生成 Reality 密钥对，返回 (private_key, public_key)。"""
    exe = _BIN_DIR / "xray"
    if exe.exists():
        try:
            out = subprocess.run(
                [str(exe), "x25519"], capture_output=True, text=True, timeout=15
            ).stdout
            priv = pub = ""
            for line in out.splitlines():
                if line.startswith("PrivateKey:"):
                    priv = line.split(":", 1)[1].strip()
                elif line.startswith("Password (PublicKey):"):
                    pub = line.split(":", 1)[1].strip()
            if priv and pub:
                return priv, pub
        except Exception:
            pass
    return "", ""


def generate_credentials() -> dict:
    """生成一台服务器的完整独立凭据。"""
    priv, pub = _x25519_keypair()
    return {
        "proxy_user": "ydus" + _rand(6).lower(),
        "proxy_pass": _rand(16),
        "proxy_port": 1080,
        "ss_password": _rand(16),
        "ss_port": 45678,
        "root_password": _rand(16),
        "reality_uuid": str(_uuid.uuid4()),
        "reality_private_key": priv,
        "reality_public_key": pub,
        "reality_short_id": secrets.token_hex(4),
        "reality_sni": "www.cloudflare.com",
        "reality_port": 443,
        "hysteria_password": _rand(16),
        "hysteria_port": 8443,
        "hysteria_mport": "20000-20100",
    }


def generate_password(n: int = 16) -> str:
    """生成一个随机密码（字母+数字）。"""
    return _rand(n)


def generate_reality() -> dict:
    """生成一组配对的 Reality 凭据：UUID + 私钥 + 公钥 + shortId。

    私钥/公钥用 xray x25519 一次生成，天然配对，保证 Reality 可用。
    """
    priv, pub = _x25519_keypair()
    return {
        "reality_uuid": str(_uuid.uuid4()),
        "reality_private_key": priv,
        "reality_public_key": pub,
        "reality_short_id": secrets.token_hex(4),
    }


def _server_cfg(server_id: str) -> dict:
    return dict(config.get("server_configs", {}).get(server_id, {}))


def save_credentials(server_id: str, creds: dict) -> None:
    sc = dict(config.get("server_configs", {}))
    sc[server_id] = dict(creds)
    config.set_value("server_configs", sc)


def get_credentials(server_id: str) -> dict:
    """返回某台服务器的完整凭据（per-server 覆盖全局默认）。"""
    merged = {
        "proxy_user": config.get("proxy_user", ""),
        "proxy_pass": config.get("proxy_pass", ""),
        "proxy_port": config.get("proxy_port", 1080),
        "ss_password": config.get("ss_password", ""),
        "ss_port": config.get("ss_port", 45678),
        "root_password": config.get("root_password", ""),
        "reality_uuid": config.get("reality_uuid", ""),
        "reality_private_key": config.get("reality_private_key", ""),
        "reality_public_key": config.get("reality_public_key", ""),
        "reality_short_id": config.get("reality_short_id", ""),
        "reality_sni": config.get("reality_sni", "www.cloudflare.com"),
        "reality_port": config.get("reality_port", 443),
        "hysteria_password": config.get("hysteria_password", ""),
        "hysteria_port": config.get("hysteria_port", 8443),
        "hysteria_mport": config.get("hysteria_mport", "20000-20100"),
    }
    merged.update(_server_cfg(server_id))
    return merged


def get_credential(server_id: str, key: str, default=None):
    """单字段读取：per-server 优先，fallback 全局 config。"""
    v = _server_cfg(server_id).get(key)
    if v in (None, ""):
        v = config.get(key, default)
    return v

"""Shadowsocks AEAD 客户端（aes-256-gcm）。

实现标准 SS AEAD 协议（RFC 草案），用于本地代理直连服务器 SS 端口：
- 密钥派生：master = EVP_BytesToKey(password)，subkey = HKDF-SHA1(master, salt, "ss-subkey")
- nonce = 12 字节小端计数器，从 0 递增
- 记录格式：[加密的 2 字节长度 + 16 tag][加密的 payload + 16 tag]
"""

from __future__ import annotations

import asyncio
import hashlib
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

KEY_LEN = 32  # aes-256
SALT_LEN = 32
TAG_LEN = 16
MAX_PAYLOAD = 0x3FFF  # 16383


def _evp_bytes_to_key(password: bytes, key_len: int) -> bytes:
    """EVP_BytesToKey（MD5 迭代，无 salt）。"""
    m = []
    d = b""
    while len(b"".join(m)) < key_len:
        d = hashlib.md5(d + password).digest()
        m.append(d)
    return b"".join(m)[:key_len]


def _subkey(master_key: bytes, salt: bytes) -> bytes:
    hkdf = HKDF(algorithm=hashes.SHA1(), length=len(master_key), salt=salt, info=b"ss-subkey")
    return hkdf.derive(master_key)


def _nonce(n: int) -> bytes:
    return n.to_bytes(12, "little")


class SSWriter:
    """加密写方向（客户端 → 服务器）。"""

    def __init__(self, writer: asyncio.StreamWriter, aead: AESGCM):
        self._w = writer
        self._aead = aead
        self._nonce = 0

    async def write(self, data: bytes) -> None:
        if not data:
            return
        for i in range(0, len(data), MAX_PAYLOAD):
            chunk = data[i : i + MAX_PAYLOAD]
            self._w.write(self._aead.encrypt(_nonce(self._nonce), len(chunk).to_bytes(2, "big"), None))
            self._nonce += 1
            self._w.write(self._aead.encrypt(_nonce(self._nonce), chunk, None))
            self._nonce += 1
        await self._w.drain()

    def close(self) -> None:
        try:
            self._w.close()
        except Exception:
            pass


class SSReader:
    """解密读方向（服务器 → 客户端）。首次 read 时才读服务器 salt 并派生 subkey。"""

    def __init__(self, reader: asyncio.StreamReader, make_aead):
        self._r = reader
        self._make = make_aead  # 函数：salt -> AESGCM
        self._aead = None
        self._nonce = 0

    async def _ensure_aead(self) -> None:
        if self._aead is None:
            salt = await self._r.readexactly(SALT_LEN)
            self._aead = self._make(salt)

    async def read(self, n: int = -1) -> bytes:
        await self._ensure_aead()
        try:
            len_enc = await self._r.readexactly(2 + TAG_LEN)
        except asyncio.IncompleteReadError:
            return b""
        length = int.from_bytes(self._aead.decrypt(_nonce(self._nonce), len_enc, None), "big")
        self._nonce += 1
        if length == 0:
            return b""
        data_enc = await self._r.readexactly(length + TAG_LEN)
        data = self._aead.decrypt(_nonce(self._nonce), data_enc, None)
        self._nonce += 1
        return data


def make_cipher(password: str, salt: bytes) -> AESGCM:
    """从密码 + salt 构造 AESGCM 加解密对象。"""
    master = _evp_bytes_to_key(password.encode(), KEY_LEN)
    return AESGCM(_subkey(master, salt))


def new_salt() -> bytes:
    return os.urandom(SALT_LEN)

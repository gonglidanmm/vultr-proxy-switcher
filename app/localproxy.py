"""本地 SOCKS5 转发代理（核心「随时切换」机制）。

在 127.0.0.1:local_port 起一个 SOCKS5 服务，把进来的连接转发到当前选中的
上游 SOCKS5 服务器。切换上游 = 换一个 Upstream 对象，新连接立即走新 IP，
用户的应用永远填 127.0.0.1:1080，无需改动。

- 客户端侧握手：SOCKS5 无认证（只监听回环，不暴露到外网）。
- 上游侧握手：SOCKS5 + RFC1929 用户名密码（gost 需要）。
- 只支持 TCP CONNECT（浏览器/常见软件够用）。
"""

from __future__ import annotations

import asyncio
import ipaddress
import threading
from dataclasses import dataclass

from . import ssclient


class Socks5Error(Exception):
    pass


@dataclass
class Upstream:
    """一个上游服务器（远程 gost/xray/hysteria）。"""

    host: str
    port: int
    user: str = ""
    password: str = ""
    name: str = ""  # 展示名
    protocol: str = "socks5"  # socks5 / ss（reality/hysteria2 走本地客户端，仍用 socks5）


def build_addr(host: str, port: int) -> bytes:
    """构造 SOCKS5 地址段：ATYP(1=IPv4 / 3=域名 / 4=IPv6) + ADDR + PORT。"""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        h = host.encode("idna")
        if len(h) > 255:
            raise Socks5Error("域名过长")
        return b"\x03" + bytes([len(h)]) + h + port.to_bytes(2, "big")
    if isinstance(ip, ipaddress.IPv4Address):
        return b"\x01" + ip.packed + port.to_bytes(2, "big")
    return b"\x04" + ip.packed + port.to_bytes(2, "big")


async def _read_addr_body(reader: asyncio.StreamReader, atyp: int) -> tuple[str, int]:
    if atyp == 0x01:
        host = str(ipaddress.IPv4Address(await reader.readexactly(4)))
    elif atyp == 0x04:
        host = str(ipaddress.IPv6Address(await reader.readexactly(16)))
    elif atyp == 0x03:
        ln = (await reader.readexactly(1))[0]
        host = (await reader.readexactly(ln)).decode("idna")
    else:
        raise Socks5Error(f"不支持的地址类型 {atyp}")
    port = int.from_bytes(await reader.readexactly(2), "big")
    return host, port


class LocalSocks5Proxy:
    """本地转发代理。线程安全地保存当前上游，异步处理连接。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 1080):
        self.host = host
        self.port = port
        self._upstream: Upstream | None = None
        self._lock = threading.Lock()
        self._server: asyncio.Server | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: set[asyncio.Task] = set()

    # ---- 上游切换（线程安全） ----
    def set_upstream(self, up: Upstream | None) -> None:
        with self._lock:
            self._upstream = up

    def get_upstream(self) -> Upstream | None:
        with self._lock:
            return self._upstream

    # ---- 生命周期 ----
    async def serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._server = await asyncio.start_server(self._on_conn, self.host, self.port)
        try:
            async with self._server:
                await self._server.serve_forever()
        except asyncio.CancelledError:
            # 正常停机：stop() 里 close() 会取消 serve_forever 的等待 future
            pass
        finally:
            # 干净退出：取消所有在途连接任务，避免 "Task was destroyed but pending"
            for t in list(self._tasks):
                t.cancel()
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
            self._server = None

    def stop(self) -> None:
        loop = self._loop
        server = self._server
        if loop is not None and server is not None:
            loop.call_soon_threadsafe(server.close)

    # ---- 连接处理 ----
    async def _on_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        try:
            await self._handle(reader, writer)
        except (asyncio.IncompleteReadError, ConnectionError, OSError, Socks5Error):
            pass
        except Exception:
            pass
        finally:
            self._tasks.discard(task)
            try:
                writer.close()
            except Exception:
                pass

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # 1. 客户端问候
        ver, nmethods = await reader.readexactly(2)
        if ver != 0x05:
            return
        await reader.readexactly(nmethods)
        # 2. 回「无认证」
        writer.write(b"\x05\x00")
        await writer.drain()
        # 3. 请求
        head = await reader.readexactly(4)
        if head[0] != 0x05 or head[1] != 0x01:  # 只支持 CONNECT
            writer.write(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")  # command not supported
            await writer.drain()
            return
        host, port = await _read_addr_body(reader, head[3])
        # 4. 连上游
        up = self.get_upstream()
        if up is None:
            writer.write(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")  # general failure
            await writer.drain()
            return
        try:
            if up.protocol == "ss":
                ur, uw, ss_reader, ss_writer = await self._connect_ss_upstream(up, host, port)
            else:
                ur, uw = await self._connect_upstream(up, host, port)
        except Exception:
            writer.write(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")  # connection refused
            await writer.drain()
            return
        # 5. 回成功
        writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        await writer.drain()
        # 6. 双向转发
        if up.protocol == "ss":
            await self._relay_ss(reader, writer, ur, uw, ss_reader, ss_writer)
        else:
            await self._relay(reader, writer, ur, uw)

    async def _connect_upstream(
        self, up: Upstream, host: str, port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        reader, writer = await asyncio.open_connection(up.host, up.port)
        try:
            methods = b"\x05\x02\x00\x02" if up.user else b"\x05\x01\x00"
            writer.write(methods)
            await writer.drain()
            resp = await reader.readexactly(2)
            if resp[0] != 0x05:
                raise Socks5Error("上游协议错误")
            chosen = resp[1]
            if chosen == 0x02:
                u = up.user.encode()
                p = up.password.encode()
                writer.write(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
                await writer.drain()
                ar = await reader.readexactly(2)
                if ar[0] != 0x01 or ar[1] != 0x00:
                    raise Socks5Error("上游认证失败")
            elif chosen != 0x00:
                raise Socks5Error(f"上游无可用认证方式 {chosen}")
            writer.write(b"\x05\x01\x00" + build_addr(host, port))
            await writer.drain()
            head = await reader.readexactly(4)
            if head[0] != 0x05 or head[1] != 0x00:
                raise Socks5Error(f"上游拒绝连接 {head[1]}")
            await _read_addr_body(reader, head[3])  # 跳过 BND 地址
            return reader, writer
        except Exception:
            writer.close()
            raise

    async def _connect_ss_upstream(
        self, up: Upstream, host: str, port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, "ssclient.SSReader", "ssclient.SSWriter"]:
        """连 SS 上游（aes-256-gcm），返回 (原始reader, 原始writer, 解密reader, 加密writer)。"""
        reader, writer = await asyncio.open_connection(up.host, up.port)
        try:
            # 写方向：客户端生成 salt，写 salt，用 subkey 加密
            salt_w = ssclient.new_salt()
            aead_w = ssclient.make_cipher(up.password, salt_w)
            writer.write(salt_w)
            await writer.drain()
            ss_writer = ssclient.SSWriter(writer, aead_w)
            # 读方向：服务器会生成自己的 salt 并发来，延迟到首次 read 时再读
            ss_reader = ssclient.SSReader(reader, lambda s: ssclient.make_cipher(up.password, s))
            # 发送目标地址（第一个加密块）
            await ss_writer.write(build_addr(host, port))
            return reader, writer, ss_reader, ss_writer
        except Exception:
            writer.close()
            raise

    async def _relay_ss(
        self,
        cr: asyncio.StreamReader,
        cw: asyncio.StreamWriter,
        ur: asyncio.StreamReader,
        uw: asyncio.StreamWriter,
        ss_reader: "ssclient.SSReader",
        ss_writer: "ssclient.SSWriter",
    ) -> None:
        """SS 上游双向转发：客户端明文 ↔ 服务器密文。"""

        async def c2s() -> None:
            try:
                while True:
                    data = await cr.read(65536)
                    if not data:
                        break
                    await ss_writer.write(data)
            except (ConnectionError, asyncio.IncompleteReadError, OSError):
                pass
            finally:
                ss_writer.close()

        async def s2c() -> None:
            try:
                while True:
                    data = await ss_reader.read()
                    if not data:
                        break
                    cw.write(data)
                    await cw.drain()
            except (ConnectionError, asyncio.IncompleteReadError, OSError):
                pass
            finally:
                try:
                    cw.close()
                except Exception:
                    pass

        try:
            await asyncio.gather(c2s(), s2c(), return_exceptions=True)
        finally:
            for w in (cw, uw):
                try:
                    w.close()
                except Exception:
                    pass

    @staticmethod
    async def _relay(
        cr: asyncio.StreamReader,
        cw: asyncio.StreamWriter,
        ur: asyncio.StreamReader,
        uw: asyncio.StreamWriter,
    ) -> None:
        async def pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while True:
                    data = await src.read(65536)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, asyncio.IncompleteReadError, OSError):
                pass
            finally:
                # 读到 EOF（对端关闭）就关闭本方向写侧，把关闭信号传播出去，
                # 否则双向 relay 会互相等对方先关 → 死锁。
                try:
                    dst.close()
                except Exception:
                    pass

        try:
            await asyncio.gather(pipe(cr, uw), pipe(ur, cw), return_exceptions=True)
        finally:
            for w in (cw, uw):
                try:
                    w.close()
                except Exception:
                    pass

"""端到端测试：验证本地 SOCKS5 转发代理真实可用。

链路：测试客户端 → 本地代理(127.0.0.1:1080) → 上游 SOCKS5(带认证) → 目标 HTTP。
另验证「切换上游」立即生效。用裸 asyncio 客户端，可打印每一步定位。

运行：uv run python scripts/test_localproxy.py
"""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.localproxy import LocalSocks5Proxy, Upstream, _read_addr_body, build_addr

BODY = b"OK-proxy-test"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, *args) -> None:
        pass


async def _upstream_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    ver, n = await reader.readexactly(2)
    await reader.readexactly(n)
    writer.write(b"\x05\x02")  # 要求用户名密码认证
    await writer.drain()
    _, ulen = await reader.readexactly(2)
    user = await reader.readexactly(ulen)
    plen = (await reader.readexactly(1))[0]
    pw = await reader.readexactly(plen)
    if user == b"yduas4980" and pw == b"a9966996":
        writer.write(b"\x01\x00")
    else:
        writer.write(b"\x01\x01")
        writer.close()
        return
    await writer.drain()
    head = await reader.readexactly(4)
    host, port = await _read_addr_body(reader, head[3])
    try:
        tr, tw = await asyncio.open_connection(host, port)
    except OSError:
        writer.write(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")
        await writer.drain()
        writer.close()
        return
    writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
    await writer.drain()

    async def pipe(s: asyncio.StreamReader, d: asyncio.StreamWriter) -> None:
        try:
            while True:
                data = await s.read(65536)
                if not data:
                    break
                d.write(data)
                await d.drain()
        except (ConnectionError, asyncio.IncompleteReadError, OSError):
            pass
        finally:
            try:
                d.close()
            except Exception:
                pass

    try:
        await asyncio.gather(pipe(reader, tw), pipe(tr, writer), return_exceptions=True)
    finally:
        for w in (writer, tw):
            try:
                w.close()
            except Exception:
                pass


async def socks5_get(proxy_port: int, host: str, port: int) -> tuple[str, str]:
    """裸 SOCKS5 客户端：走代理发一个 GET，返回 (响应文本, 错误码)。"""
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    try:
        writer.write(b"\x05\x01\x00")  # 问候：无认证
        await writer.drain()
        resp = await asyncio.wait_for(reader.readexactly(2), 5)
        print(f"    [client] 问候应答 {resp.hex()}")
        writer.write(b"\x05\x01\x00" + build_addr(host, port))  # CONNECT
        await writer.drain()
        head = await asyncio.wait_for(reader.readexactly(4), 5)
        print(f"    [client] CONNECT 应答 {head.hex()}")
        if head[1] != 0x00:
            return "", f"REP={head[1]}"
        await _read_addr_body(reader, head[3])
        req = f"GET / HTTP/1.0\r\nHost: {host}\r\n\r\n".encode()
        writer.write(req)
        await writer.drain()
        data = await asyncio.wait_for(reader.read(), 5)
        return data.decode(), ""
    finally:
        writer.close()


async def main() -> None:
    httpd = HTTPServer(("127.0.0.1", 18080), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    up_srv = await asyncio.start_server(_upstream_handler, "127.0.0.1", 11080)

    lp = LocalSocks5Proxy("127.0.0.1", 1080)
    lp.set_upstream(Upstream("127.0.0.1", 11080, "yduas4980", "a9966996", "test-upstream"))
    lp_task = asyncio.create_task(lp.serve())
    await asyncio.sleep(0.5)

    print("[1] 正常转发：")
    out, err = await socks5_get(1080, "127.0.0.1", 18080)
    print(f"    -> 响应 {out.strip()!r} err={err!r}")
    assert BODY.decode() in out, f"转发失败: err={err} out={out!r}"

    print("[2] 切换到死上游：")
    lp.set_upstream(Upstream("127.0.0.1", 19999, "", "", "dead"))
    out2, err2 = await socks5_get(1080, "127.0.0.1", 18080)
    print(f"    -> err={err2!r} (应为 REP=5 或连接失败)")
    assert err2 != "", "切换未生效，仍连旧上游"

    print("[3] 切回：")
    lp.set_upstream(Upstream("127.0.0.1", 11080, "yduas4980", "a9966996", "test-upstream"))
    out3, err3 = await socks5_get(1080, "127.0.0.1", 18080)
    print(f"    -> 响应 {out3.strip()!r} err={err3!r}")
    assert BODY.decode() in out3, "切回失败"

    lp.stop()
    up_srv.close()
    await up_srv.wait_closed()
    await lp_task
    httpd.shutdown()
    print("ALL OK ✅")


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=30))

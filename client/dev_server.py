#!/usr/bin/env python3
"""无缓存静态服务器：启动前自动清 webview 缓存 + 强制返回最新文件。"""
import http.server
import functools
import os
import shutil

LOG = "/tmp/dev_server.log"

# 启动前清 webview 缓存（避免旧前端缓存导致刷新不生效）
for d in (
    os.path.expanduser("~/Library/WebKit/proxy-client"),
    os.path.expanduser("~/Library/Caches/proxy-client"),
):
    try:
        shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass

class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        with open(LOG, "a") as f:
            f.write(fmt % args + "\n")
    def do_GET(self):
        for h in ("If-Modified-Since", "If-None-Match", "If-Match"):
            if h in self.headers:
                del self.headers[h]
        super().do_GET()
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

handler = functools.partial(NoCacheHandler, directory="src")
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 1424), handler)
print("静态服务器已启动: http://127.0.0.1:1424 (no-cache, 已清缓存)")
srv.serve_forever()

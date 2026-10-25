// SwitchRoom Durable Object：每个用户一个实例，维护该用户所有 WebSocket 连接，广播切换进度。
// 用法：
//   - 客户端 WebSocket 连 /ws（Worker 路由到 env.SWITCH_ROOM.get(idFromName("u"+user_id))）
//   - Worker 状态变化时调 notifySwitch() → POST 到 DO 的 /broadcast → 广播给该用户所有连接

export class SwitchRoom {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
    this.sessions = new Set(); // server-side WebSocket 集合
  }

  async fetch(request) {
    const url = new URL(request.url);

    // WebSocket 升级（客户端连接）
    if (request.headers.get("Upgrade") === "websocket") {
      const pair = new WebSocketPair();
      const client = pair[0];
      const server = pair[1];
      this.sessions.add(server);
      server.accept();
      server.addEventListener("message", () => { /* 心跳/忽略 */ });
      server.addEventListener("close", () => this.sessions.delete(server));
      server.addEventListener("error", () => this.sessions.delete(server));
      return new Response(null, { status: 101, webSocket: client });
    }

    // HTTP 通知（Worker 调 notifySwitch 发来）→ 广播给所有连接
    if (request.method === "POST" && url.pathname === "/broadcast") {
      const payload = await request.text().catch(() => "");
      this.broadcast(payload);
      return new Response("ok", { status: 200 });
    }

    return new Response("Not Found", { status: 404 });
  }

  broadcast(payload) {
    for (const ws of [...this.sessions]) {
      try {
        ws.send(payload);
      } catch (_) {
        this.sessions.delete(ws);
      }
    }
  }
}

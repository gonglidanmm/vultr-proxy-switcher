// WebSocket 实时推送客户端（切换进度实时回显）
// 连接 /ws，收 SwitchRoom 广播的切换状态变化消息；断线自动重连。
import { API, TOKEN_KEY } from "./index.js";

let ws = null;
let handlers = new Set(); // 消息处理器集合
let reconnectTimer = null;

function wsUrl() {
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) return null;
  const base = API.replace(/^https?:\/\//, "");
  const proto = API.startsWith("https") ? "wss" : "ws";
  return `${proto}://${base}/ws?token=${encodeURIComponent(token)}`;
}

export function connectWs() {
  const url = wsUrl();
  if (!url) return;
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  try {
    ws = new WebSocket(url);
  } catch (_) {
    scheduleReconnect();
    return;
  }
  ws.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data);
      handlers.forEach((h) => { try { h(data); } catch (_) {} });
    } catch (_) { /* 忽略非 JSON */ }
  };
  ws.onclose = () => scheduleReconnect();
  ws.onerror = () => { try { ws.close(); } catch (_) {} };
}

function scheduleReconnect() {
  ws = null;
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectWs();
  }, 5000);
}

// 订阅切换消息，返回取消订阅函数
export function onSwitchMessage(handler) {
  handlers.add(handler);
  return () => handlers.delete(handler);
}

export function closeWs() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  if (ws) { try { ws.close(); } catch (_) {} }
  ws = null;
}

// API 层封装（从旧 main.js 迁移，逻辑不变）
export const API = "https://user.yduas.cc";
export const TOKEN_KEY = "proxy_client_token";
export const USER_KEY = "proxy_client_user";

export async function api(path, method = "GET", body = null, auth = true) {
  const token = localStorage.getItem(TOKEN_KEY) || "";
  const headers = { "Content-Type": "application/json" };
  if (auth && token) headers["Authorization"] = "Bearer " + token;
  const resp = await fetch(API + path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : null,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const err = new Error(data.error || "请求失败 HTTP " + resp.status);
    err.status = resp.status;
    throw err;
  }
  return data;
}

// Rust 层调用（Tauri command，本地系统操作）
export async function invokeRust(cmd, args = {}) {
  if (!window.__TAURI__ || !window.__TAURI__.core) {
    throw new Error("本地功能不可用（需在桌面客户端运行）");
  }
  return await window.__TAURI__.core.invoke(cmd, args);
}

// 复制文本（带反馈 + fallback）
export async function copyText(text) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
    }
    return true;
  } catch (_) {
    return false;
  }
}

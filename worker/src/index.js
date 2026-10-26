import { buildGostUserData } from "./gost.js";
import { REGION_COUNTRY, REGION_CITY, COUNTRY_NAMES } from "./region.js";
import { SwitchRoom } from "./switch-room.js";
export { SwitchRoom };
import indexHtml from "../assets/index.html";
import mainJs from "../assets/main.js" with { type: "text" };
import styleCss from "../assets/style.css" with { type: "text" };

// 客户端版本管理：发新客户端（Rust）版本时改这里，前端据此提示老客户端升级
const CLIENT_VERSION = "0.1.2";
const CLIENT_DOWNLOAD_URL = "https://ghfast.top/https://github.com/gonglidanmm/vultr-proxy-switcher/releases/latest";
const CLIENT_RELEASE_NOTE = "切换IP提速、列表拖拽排序/列宽调整、侧边栏+用户管理、多项稳定性修复";
// 前端构建时间戳（从 index.html 的 ?v= 参数解析，部署时自动更新；用于客户端热更新检测）
const FRONTEND_STAMP = (indexHtml.match(/\?v=(\d+)/) || [])[1] || "0";
function apiBase(env, kind) {
  const c = env.CONTROL_URL || "";
  return c ? `${c}/${kind}` : (kind === "vultr" ? "https://api.vultr.com" : "https://api.cloudflare.com");
}
function apiHeaders(env, kind) {
  if (env.CONTROL_URL) {
    return { "X-Control-Secret": env.CONTROL_SECRET || "", "Content-Type": "application/json", "User-Agent": "curl/8.4.0" };
  }
  const token = kind === "vultr" ? env.VULTR_API_KEY : env.CF_TOKEN;
  return { "Authorization": `Bearer ${token || ""}`, "Content-Type": "application/json", "User-Agent": "curl/8.4.0" };
}

// 北京时间「今天」（UTC+8），switch_date 按北京时间计，避免跨 8 小时时区导致「今天」判断偏差
function bjToday() {
  return new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 10);
}

// 生成分享链接（补 links 用）：域名不变（卖点：切 IP 域名自动指向新 IP），links 的 host 用域名。
// 与 app/share.py 保持一致：SS 用 websafe-base64 带 padding（SIP002），hysteria2 端口部分用 mport 范围。
function b64url(s) {
  return btoa(unescape(encodeURIComponent(s))).replace(/\+/g, "-").replace(/\//g, "_");
}
function buildLinks(nodeData) {
  const dom = (nodeData && nodeData.domain) || "";
  const s = (nodeData && nodeData.socks5) || {};
  const ss = (nodeData && nodeData.ss) || {};
  const rl = (nodeData && nodeData.reality) || {};
  const hy = (nodeData && nodeData.hysteria2) || {};
  if (!dom || !s.password || !ss.password) return null;  // 凭据不全，不生成
  return {
    socks5: `socks5://${s.username || ""}:${s.password}@${dom}:${s.port || 1080}#SOCKS5`,
    ss: `ss://${b64url(`${ss.method || "aes-256-gcm"}:${ss.password}`)}@${dom}:${ss.port || 45678}#SS`,
    reality: `vless://${rl.uuid || ""}@${dom}:${rl.port || 443}?encryption=none&flow=${rl.flow || "xtls-rprx-vision"}&security=reality&sni=${rl.sni || "www.cloudflare.com"}&fp=${rl.fp || "chrome"}&pbk=${rl.public_key || ""}&sid=${rl.short_id || ""}&type=tcp#Reality`,
    hysteria2: `hysteria2://${hy.password || ""}@${dom}:${hy.mport || hy.port || 8443}/#Hysteria2`,
  };
}

// Cloudflare Worker: vultr-proxy-users API
// IP 代理客户管理服务端
//
// 端点：
//   客户端（用户 token）：
//     POST /login                登录，返回 token
//     GET  /me/nodes             查询自己名下的节点
//     POST /me/account           改用户名/密码（需当前密码）
//   管理端（X-Admin-Token 头）：
//     GET    /admin/users        用户列表（含分配数量）
//     POST   /admin/users        创建用户
//     GET    /admin/users/:id    用户详情（含分配列表）
//     PUT    /admin/users/:id    更新用户
//     DELETE /admin/users/:id    删除用户（级联删分配+会话）
//     POST   /admin/assignments  分配资源
//     DELETE /admin/assignments  取消分配
//     GET    /admin/stats        统计
//
// 安全：
//   - 密码 PBKDF2-SHA256（10000 次迭代 + 16 字节随机盐），存 salt$hash
//   - 管理员 X-Admin-Token 头，timing-safe 比较（token 存在 Worker secret）
//   - 用户 token 随机 32 字节，存 sessions 表，30 天过期

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Admin-Token",
};

const json = (data, status = 200, extraHeaders = {}) =>
  new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      ...CORS,
      ...extraHeaders,
    },
  });

// ============ 密码哈希（PBKDF2-SHA256） ============

function hexToBytes(hex) {
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < bytes.length; i++) {
    bytes[i] = parseInt(hex.substr(i * 2, 2), 16);
  }
  return bytes;
}

function bytesToHex(bytes) {
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function pbkdf2(password, saltHex, iterations) {
  const enc = new TextEncoder();
  const keyMaterial = await crypto.subtle.importKey(
    "raw",
    enc.encode(password),
    { name: "PBKDF2" },
    false,
    ["deriveBits"],
  );
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", salt: hexToBytes(saltHex), iterations, hash: "SHA-256" },
    keyMaterial,
    256,
  );
  return bytesToHex(new Uint8Array(bits));
}

async function hashPassword(password) {
  const salt = bytesToHex(crypto.getRandomValues(new Uint8Array(16)));
  const hash = await pbkdf2(password, salt, 10000);
  return `${salt}$${hash}`;
}

async function verifyPassword(password, stored) {
  if (typeof stored !== "string") return false;
  const idx = stored.indexOf("$");
  if (idx < 0) return false;
  const salt = stored.slice(0, idx);
  const expected = stored.slice(idx + 1);
  const computed = await pbkdf2(password, salt, 10000);
  return timingSafeEqual(computed, expected);
}

function timingSafeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) {
    return false;
  }
  let result = 0;
  for (let i = 0; i < a.length; i++) {
    result |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return result === 0;
}

function newToken() {
  return bytesToHex(crypto.getRandomValues(new Uint8Array(32)));
}

// ============ 鉴权 ============

async function requireAdmin(request, env) {
  const token = request.headers.get("X-Admin-Token") || "";
  const expected = env.ADMIN_TOKEN || "";
  if (!expected || !timingSafeEqual(token, expected)) return false;
  return true;
}

// 查证书缓存（切换 IP 复用，避免频繁签发触发 Let's Encrypt 限流）
async function getCachedCert(env, domain) {
  if (!domain) return null;
  const row = await env.DB.prepare(
    "SELECT fullchain, key, expires_at FROM certificates WHERE domain = ?",
  )
    .bind(domain)
    .first();
  if (!row || !row.fullchain || !row.key) return null;
  if (row.expires_at && new Date(row.expires_at) < new Date()) return null;  // 已过期，重新签
  return { fullchain: row.fullchain, key: row.key };
}

// 节点签好证书后回传，缓存到 D1（供下次切换 IP 复用）
async function certSave(request, env) {
  const token = request.headers.get("X-Cert-Token") || "";
  const expected = env.CERT_TOKEN || "";
  if (!expected || token !== expected) return json({ error: "认证失败" }, 401);
  const body = await request.json().catch(() => ({}));
  const domain = String(body.domain || "").trim();
  const fullchain = String(body.fullchain || "");
  const key = String(body.key || "");
  if (!domain || !fullchain || !key) return json({ error: "参数不完整" }, 400);
  const issuedAt = new Date().toISOString();
  const expiresAt = new Date(Date.now() + 90 * 24 * 3600 * 1000).toISOString();
  await env.DB.prepare(
    "INSERT INTO certificates (domain, fullchain, key, issued_at, expires_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(domain) DO UPDATE SET fullchain = excluded.fullchain, key = excluded.key, issued_at = excluded.issued_at, expires_at = excluded.expires_at",
  )
    .bind(domain, fullchain, key, issuedAt, expiresAt)
    .run();
  return json({ ok: true });
}

async function requireUser(request, env) {
  const auth = request.headers.get("Authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7).trim() : "";
  if (!token) return null;
  const row = await env.DB.prepare(
    "SELECT s.user_id AS id, u.username, u.status, u.expires_at, u.switch_limit FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ? AND s.expires_at > ?",
  )
    .bind(token, new Date().toISOString())
    .first();
  if (!row) return null;
  if (row.status !== "active") return null;
  // 注：不再按用户级 expires_at 拒绝鉴权——每台服务器独立到期（node_data.client_expiry），
  // 单台到期只禁该台切换，不应锁死整个账号登录（曾导致「1台到期→全部无法登录」）。
  // 注：switch_count/switch_date 是「每节点独立」的（assignments 表），用户级无此计数，故不在此返回
  return { id: row.id, username: row.username, switch_limit: row.switch_limit };
}

// 切换状态变化时通知该用户的 DO 广播（WebSocket 实时推送）
async function notifySwitch(env, userId, payload) {
  try {
    const id = env.SWITCH_ROOM.idFromName("u" + userId);
    await env.SWITCH_ROOM.get(id).fetch("https://switch-room/broadcast", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  } catch (_) {
    // 通知失败不影响切换主流程（前端轮询兜底）
  }
}

// ============ 客户端 handlers ============

async function handleLogin(request, env) {
  const body = await request.json().catch(() => ({}));
  const username = String(body.username || "").trim();
  const password = String(body.password || "");
  if (!username || !password) return json({ error: "用户名和密码不能为空" }, 400);

  const user = await env.DB.prepare(
    "SELECT id, username, password_hash, status, expires_at FROM users WHERE username = ?",
  )
    .bind(username)
    .first();
  if (!user) return json({ error: "用户名或密码错误" }, 401);

  const ok = await verifyPassword(password, user.password_hash);
  if (!ok) return json({ error: "用户名或密码错误" }, 401);
  if (user.status !== "active") return json({ error: "账号已禁用" }, 403);
  // 不再按用户级 expires_at 拒绝登录——每台服务器独立到期（client_expiry），单台到期只禁该台切换。

  const token = newToken();
  const expiresAt = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString();
  await env.DB.prepare(
    "INSERT INTO sessions (token, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
  )
    .bind(token, user.id, expiresAt, new Date().toISOString())
    .run();

  return json({
    token,
    user: { id: user.id, username: user.username, expires_at: user.expires_at },
  });
}

async function handleMeNodes(env, user) {
  const today = bjToday();
  const rows = await env.DB.prepare(
    "SELECT a.resource_type, a.resource_id, a.node_data, a.switch_count, a.switch_limit, a.switch_date, a.quota_gb, a.used_gb, a.note, a.created_at, COALESCE((SELECT SUM(old_used_gb) FROM switch_requests s WHERE s.assignment_id = a.id AND s.status = 'done'), 0) AS hist_gb FROM assignments a WHERE a.user_id = ? ORDER BY a.sort_order, a.id",
  )
    .bind(user.id)
    .all();
  const nodes = rows.results.map((r) => {
    let data = {};
    try {
      data = JSON.parse(r.node_data);
    } catch (_) {
      data = {};
    }
    delete data._creds;  // 不暴露完整凭据给客户端
    // 每个「用户↔资源」独立计数，按天重置
    let count = r.switch_count || 0;
    const nodeLimit = r.switch_limit == null ? 0 : Number(r.switch_limit);
    const userLimit = user && user.switch_limit != null ? Number(user.switch_limit) : 0;
    const limit = nodeLimit > 0 ? nodeLimit : (userLimit > 0 ? userLimit : 5);
    if ((r.switch_date || "") !== today) count = 0;
    // 流量配额：quota_gb > 0 才限流；用完标记 exhausted（客户端据此断流）
    // 真实已用 = 历史累计（切换链 sum old_used_gb）+ 当前实例出站（assignments.used_gb，管理端同步）
    const quota = Number(r.quota_gb) || 0;
    const used = (Number(r.hist_gb) || 0) + (Number(r.used_gb) || 0);
    return {
      resource_type: r.resource_type,
      resource_id: r.resource_id,
      ...data,
      quota_gb: quota,  // 统一用 assignments.quota_gb（覆盖 node_data 里的同名快照，避免双份漂移）
      note: r.note || "",
      created_at: r.created_at || "",  // 真实分配时间（assignments.created_at）
      switch_remaining: Math.max(0, limit - 1 - count),
      switch_limit: limit,
      flow_used_gb: used,
      flow_quota_gb: quota,
      flow_exhausted: quota > 0 && used >= quota,
    };
  });
  return json({ nodes });
}

async function handleMeAccount(request, env, user) {
  const body = await request.json().catch(() => ({}));
  const curPassword = String(body.current_password || "");
  const newPassword = String(body.new_password || "");

  if (!curPassword) return json({ error: "当前密码不能为空" }, 400);
  if (!newPassword) return json({ error: "请输入新密码" }, 400);

  const row = await env.DB.prepare(
    "SELECT id, username, password_hash, status, expires_at FROM users WHERE id = ?",
  )
    .bind(user.id)
    .first();
  if (!row) return json({ error: "用户不存在" }, 404);

  const ok = await verifyPassword(curPassword, row.password_hash);
  if (!ok) return json({ error: "当前密码错误" }, 401);

  // 用户名不允许客户端修改（只在服务端管理端修改）
  const username = row.username;

  let passwordHash = row.password_hash;
  if (newPassword) {
    if (newPassword.length < 6) return json({ error: "密码至少 6 位" }, 400);
    passwordHash = await hashPassword(newPassword);
  }

  await env.DB.prepare(
    "UPDATE users SET username = ?, password_hash = ?, updated_at = ? WHERE id = ?",
  )
    .bind(username, passwordHash, new Date().toISOString(), user.id)
    .run();

  return json({ user: { id: row.id, username, expires_at: row.expires_at } });
}

// 保存节点备注（每用户每节点独立，持久化到 D1）
async function handleMeNote(request, env, user) {
  const body = await request.json().catch(() => ({}));
  const resourceId = String(body.resource_id || "").trim();
  const note = String(body.note || "");
  if (!resourceId) return json({ error: "resource_id 必填" }, 400);

  const row = await env.DB.prepare(
    "SELECT id FROM assignments WHERE user_id = ? AND resource_id = ?",
  ).bind(user.id, resourceId).first();
  if (!row) return json({ error: "资源不存在或未分配给你" }, 404);

  await env.DB.prepare(
    "UPDATE assignments SET note = ? WHERE user_id = ? AND resource_id = ?",
  ).bind(note, user.id, resourceId).run();

  return json({ ok: true, note });
}

// 保存节点显示顺序（客户端拖拽排序后持久化）
async function handleMeReorder(request, env, user) {
  const body = await request.json().catch(() => ({}));
  const orderedIds = Array.isArray(body.ordered_ids) ? body.ordered_ids.map(String) : [];
  if (!orderedIds.length) return json({ error: "ordered_ids 必填" }, 400);

  const stmts = [];
  for (let i = 0; i < orderedIds.length; i++) {
    stmts.push(env.DB.prepare(
      "UPDATE assignments SET sort_order = ? WHERE user_id = ? AND resource_id = ?",
    ).bind(i, user.id, orderedIds[i]));
  }
  await env.DB.batch(stmts);

  return json({ ok: true });
}


async function switchResource(request, env, user) {
  const body = await request.json().catch(() => ({}));
  const resourceId = (body.resource_id || "").trim();
  const targetRegion = (body.region || "").trim();  // 可选：客户端指定目标地区
  if (!resourceId) return json({ error: "resource_id 必填" }, 400);

  const row = await env.DB.prepare(
    "SELECT * FROM assignments WHERE user_id = ? AND resource_id = ?",
  ).bind(user.id, resourceId).first();
  if (!row) return json({ error: "资源不存在或未分配给你" }, 404);
  if (row.resource_type !== "vultr") {
    return json({ error: "未知资源类型" }, 400);
  }

  const today = bjToday();
  let count = row.switch_count || 0;
  // 节点级 switch_limit 为 0/空 时沿用用户级（每台独立上限覆盖用户级；未单独设置则跟随用户级）
  const nodeLimit = row.switch_limit == null ? 0 : Number(row.switch_limit);
  const userLimit = user.switch_limit == null ? 0 : Number(user.switch_limit);
  const limit = nodeLimit > 0 ? nodeLimit : (userLimit > 0 ? userLimit : 5);
  if ((row.switch_date || "") !== today) count = 0;

  // 到期检查：客户到期（client_expiry，可调低）与服务器到期（expiry）取更早者，到期后禁止切换
  let nd0 = {};
  try { nd0 = JSON.parse(row.node_data || "{}"); } catch (_) {}
  const expiry = nd0.expiry || "";
  const clientExpiry = nd0.client_expiry || "";
  let effectiveExpiry = expiry;
  if (clientExpiry && (!effectiveExpiry || clientExpiry < effectiveExpiry)) {
    effectiveExpiry = clientExpiry;
  }
  if (effectiveExpiry && effectiveExpiry < today) {
    return json({ error: "该节点已到期，无法切换 IP", expired: true }, 403);
  }

  if (count >= limit) {
    return json({ error: `今天切换次数已达上限（${limit} 个 IP/天）`, remaining: 0 }, 429);
  }

  const VULTR_KEY = env.VULTR_API_KEY || "";
  if (!VULTR_KEY) return json({ error: "服务端未配置 Vultr API key" }, 500);
  let creds = {};
  try {
    const nd = JSON.parse(row.node_data || "{}");
    creds = (nd._creds && typeof nd._creds === "object" && Object.keys(nd._creds).length > 0)
      ? nd._creds
      : JSON.parse(env.GLOBAL_CREDS || "{}");
  } catch (_) {
    try { creds = JSON.parse(env.GLOBAL_CREDS || "{}"); } catch (_) {}
  }

  const headers = apiHeaders(env, "vultr");

  const oldResp = await fetch(`${apiBase(env, "vultr")}/v2/instances/${resourceId}`, { headers });
  if (!oldResp.ok) return json({ error: `查询旧实例失败 HTTP ${oldResp.status}` }, 500);
  const oldInst = ((await oldResp.json()).instance) || {};
  const oldRegion = oldInst.region || "";
  const plan = oldInst.plan || "";
  const label = oldInst.label || "vultr-proxy-node";
  const country = REGION_COUNTRY[oldRegion] || "";

  // 查该套餐可用的地区（目标地区必须在套餐可用范围内，否则 fallback 随机）
  let planLocations = [];
  try {
    const planResp = await fetch(`${apiBase(env, "vultr")}/v2/plans/${plan}`, { headers });
    if (planResp.ok) {
      const planData = await planResp.json();
      planLocations = (planData.plan && planData.plan.locations) || [];
    }
  } catch (_) {}

  let newRegion;
  if (targetRegion && planLocations.includes(targetRegion) && REGION_COUNTRY[targetRegion] === country && targetRegion !== oldRegion) {
    newRegion = targetRegion;  // 客户端指定了同国家且套餐可用的地区
  } else {
    let candidates = Object.keys(REGION_COUNTRY).filter((r) => r !== oldRegion && REGION_COUNTRY[r] === country);
    if (planLocations.length > 0) candidates = candidates.filter((r) => planLocations.includes(r));
    if (candidates.length === 0) candidates = Object.keys(REGION_COUNTRY).filter((r) => REGION_COUNTRY[r] === country);
    newRegion = candidates.length ? candidates[Math.floor(Math.random() * candidates.length)] : oldRegion;
  }

  // 先创建新机，成功后再销毁旧机（避免创建失败导致旧机已销毁、节点丢失）
  const domainName = nd0.domain || "";
  const cert = await getCachedCert(env, domainName);
  const workerUrl = new URL(request.url).origin;
  const certToken = env.CERT_TOKEN || "";
  const userData = buildGostUserData(creds, domainName, cert, workerUrl, certToken);
  const createResp = await fetch(apiBase(env, "vultr") + "/v2/instances", {
    method: "POST",
    headers,
    body: JSON.stringify({ region: newRegion, plan, os_id: 1743, label, user_data: userData, backups: "disabled" }),
  });
  if (!createResp.ok) {
    const errText = await createResp.text().catch(() => "");
    return json({ error: `创建新机失败 HTTP ${createResp.status}${errText ? "：" + errText.slice(0, 200) : ""}` }, 500);
  }
  let newInst = {};
  try {
    newInst = ((await createResp.json()).instance) || {};
  } catch (_) {
    return json({ error: "创建新机响应异常，请重试" }, 500);
  }
  const newId = newInst.id || "";
  if (!newId) {
    return json({ error: "创建新机失败：未返回实例 ID" }, 500);
  }

  // 销毁旧机前：抓旧机出站流量（Vultr 不保留已销毁实例的流量明细，必须先抓再销毁）
  let oldUsedGb = 0;
  try {
    const bwResp = await fetch(`${apiBase(env, "vultr")}/v2/instances/${resourceId}/bandwidth`, { headers });
    if (bwResp.ok) {
      const bwData = await bwResp.json();
      const bw = bwData.bandwidth || {};
      oldUsedGb = Object.values(bw).reduce((s, d) => s + (Number(d.outgoing_bytes) || 0), 0) / 1e9;
    }
  } catch (_) {}

  const delResp = await fetch(`${apiBase(env, "vultr")}/v2/instances/${resourceId}`, { method: "DELETE", headers });
  // 销毁旧机失败不阻断切换（新机已创建，返回错误会让客户端重试、重复创建新机造成更多孤儿）。
  // 记日志，旧机可后续由巡检/手动处理，避免「销毁失败→重试→孤儿新机」雪崩。
  if (!delResp.ok && delResp.status !== 404) {
    console.error(`销毁旧机 ${resourceId} 失败 HTTP ${delResp.status}（不阻断切换，新机已创建）`);
  }

  await env.DB.prepare(
    "INSERT INTO switch_requests (assignment_id, user_id, old_id, new_id, status, stage, created_at, old_used_gb) VALUES (?, ?, ?, ?, 'pending', 'booting', ?, ?)",
  ).bind(row.id, user.id, resourceId, newId, new Date().toISOString(), oldUsedGb).run();

  return json({ ok: true, status: "pending", new_id: newId, remaining: limit - count - 1, stage: "booting" });
}

async function updateDns(env, domain, ip) {
  const CF_TOKEN = env.CF_TOKEN || "";
  const CF_ZONE_ID = env.CF_ZONE_ID || "";
  if (!CF_TOKEN || !CF_ZONE_ID || !domain || !ip) return;
  // Cloudflare API 直连（api.cloudflare.com 是 Cloudflare 自家 API，Worker 可直连，不依赖控制机转发）
  const headers = { "Authorization": `Bearer ${CF_TOKEN}`, "Content-Type": "application/json", "User-Agent": "curl/8.4.0" };
  const base = "https://api.cloudflare.com";
  try {
    const q = await fetch(`${base}/client/v4/zones/${CF_ZONE_ID}/dns_records?name=${encodeURIComponent(domain)}&type=A`, { headers });
    if (!q.ok) return;
    const recs = ((await q.json()).result) || [];
    if (recs.length > 0) {
      await fetch(`${base}/client/v4/zones/${CF_ZONE_ID}/dns_records/${recs[0].id}`, {
        method: "PUT", headers, body: JSON.stringify({ type: "A", name: domain, content: ip, ttl: 60, proxied: false }),
      });
    } else {
      await fetch(`${base}/client/v4/zones/${CF_ZONE_ID}/dns_records`, {
        method: "POST", headers, body: JSON.stringify({ type: "A", name: domain, content: ip, ttl: 60, proxied: false }),
      });
    }
  } catch (_) {}
}

async function checkTcpAlive(ip, port) {
  // 检测 TCP 端口是否通：对 443 发 HTTPS，区分「连接拒绝/超时」（不通）与「TLS 握手失败」（通）。
  // reality 对非法 SNI 会转发到 dest，可能返回正常响应，故有 response 或 TLS 错误都算「通」。
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 8000);
    await fetch(`https://${ip}:${port}`, { method: "HEAD", signal: ctrl.signal, redirect: "manual" });
    clearTimeout(timer);
    return true;
  } catch (e) {
    const msg = String((e && e.message) || e || "").toLowerCase();
    if (msg.includes("fetch failed") || msg.includes("timed out") || msg.includes("timeout") || msg.includes("refused") || msg.includes("econnrefused")) {
      return false;
    }
    return true; // TLS 握手失败等 = TCP 已建立 = 通
  }
}

async function checkNodeHealthy(ip) {
  // 检测 3 个 TCP 端口（443 Reality / 45678 SS / 1080 SOCKS5），全通才算健康。
  // 8443（Hysteria2）是 UDP，不做 TCP 检测。
  const ports = [443, 45678, 1080];
  const ports_status = {};
  for (const p of ports) {
    ports_status[p] = await checkTcpAlive(ip, p);
  }
  return {
    all_ok: Object.values(ports_status).every(Boolean),
    ports: ports_status,
  };
}

async function createReplacement(request, env, req) {
  // 切换的新机卡死（443 不通）时，再创建一台同 region/plan/label 的新机，销毁卡死的新机。
  // 复用 switchResource 的创建逻辑；成功返回新机 ID，失败返回 null（如 Vultr API 限流）。
  const VULTR_KEY = env.VULTR_API_KEY || "";
  if (!VULTR_KEY) return null;
  const headers = apiHeaders(env, "vultr");

  const instResp = await fetch(`${apiBase(env, "vultr")}/v2/instances/${req.new_id}`, { headers });
  if (!instResp.ok) return null;
  const inst = ((await instResp.json()).instance) || {};
  const region = inst.region || "";
  const plan = inst.plan || "";
  const label = inst.label || "vultr-proxy-node";

  const row = await env.DB.prepare("SELECT * FROM assignments WHERE id = ?").bind(req.assignment_id).first();
  let nodeData = {};
  let creds = {};
  if (row) {
    try { nodeData = JSON.parse(row.node_data); } catch (_) {}
    creds = nodeData._creds || {};
  }
  const domain = nodeData.domain || req.domain || "";

  const cert = await getCachedCert(env, domain);
  const workerUrl = new URL(request.url).origin;
  const userData = buildGostUserData(creds, domain, cert, workerUrl, env.CERT_TOKEN || "");
  const createResp = await fetch(apiBase(env, "vultr") + "/v2/instances", {
    method: "POST",
    headers,
    body: JSON.stringify({ region, plan, os_id: 1743, label, user_data: userData, backups: "disabled" }),
  });
  if (!createResp.ok) return null;
  const newInst = ((await createResp.json()).instance) || {};
  const newId2 = newInst.id || "";
  if (!newId2) return null;

  // 销毁卡死的新机（销毁失败也继续，保留 new_id2 供下次检测）
  try {
    await fetch(`${apiBase(env, "vultr")}/v2/instances/${req.new_id}`, { method: "DELETE", headers });
  } catch (_) {}

  await env.DB.prepare(
    "UPDATE switch_requests SET new_id = ?, retry_count = retry_count + 1 WHERE id = ?",
  ).bind(newId2, req.id).run();
  return newId2;
}

async function processOneSwitch(req, env, headers, request) {
  const resp = await fetch(`${apiBase(env, "vultr")}/v2/instances/${req.new_id}`, { headers });
  if (!resp.ok) {
    // 新机 404 且超过 10 分钟 = 新机已被销毁（可能用户又切了一次，新机变旧机被销毁），标记 failed 避免永久卡 pending
    if (resp.status === 404) {
      const runningMs = Date.now() - new Date(req.created_at || Date.now()).getTime();
      if (runningMs > 600000) {
        await env.DB.prepare("UPDATE switch_requests SET status = 'failed', stage = 'failed' WHERE id = ?").bind(req.id).run();
        await notifySwitch(env, req.user_id, { resource_id: req.old_id, stage: "failed", status: "failed", note: "新机已不存在（可能被后续切换销毁）" });
        return { resource_id: req.old_id, stage: "failed", status: "failed", note: "新机已不存在（可能被后续切换销毁）" };
      }
    }
    return { resource_id: req.old_id, stage: "booting", status: "pending", note: "查询新机状态失败" };
  }
  const inst = ((await resp.json()).instance) || {};
  const power = inst.power_status || inst.status || "";
  const newIp = inst.main_ip || "";
  if (power !== "running" || !newIp) {
    return { resource_id: req.old_id, stage: "booting", status: "pending", note: "新机已创建，正在启动中" };
  }

  // 拿到新机 IP 后立即更新 DNS（不等 cloud-init 完成），让域名解析尽早生效。
  // 核心卖点：客户设一次域名，切 IP 不用改任何配置，域名自动指向新 IP。
  // 用 switch_requests.new_ip 做「DNS 已更新」标记（为空=未更新），避免每轮轮询重复调 Cloudflare API。
  if (!req.new_ip) {
    const row0 = await env.DB.prepare("SELECT * FROM assignments WHERE id = ?").bind(req.assignment_id).first();
    let nd0 = {};
    try { nd0 = JSON.parse(row0 ? row0.node_data : "{}"); } catch (_) {}
    const domain0 = nd0.domain || "";
    if (domain0) {
      await updateDns(env, domain0, newIp);
      await env.DB.prepare("UPDATE switch_requests SET new_ip = ? WHERE id = ?").bind(newIp, req.id).run();
    }
  }

  // 真实检测安装完成：等新机 cloud-init 安装完代理软件后主动回写 node-ready（stage 变 'installed'）。
  // 不再死等固定 3.5 分钟——装多久等多久（通常 1-2 分钟就回写，立即就绪），
  // 超时 5 分钟兜底（cloud-init 异常没回写时，按就绪处理，客户端会真实检测端口，不通自动重切）。
  if (req.stage !== "installed") {
    const runningMs = Date.now() - new Date(req.created_at || Date.now()).getTime();
    if (runningMs < 300000) {
      return { resource_id: req.old_id, stage: "installing", status: "pending", note: "新机运行中，正在下载安装代理软件（真实等待安装完成）" };
    }
  }

  // 抢占（防并发重复处理）：多台客户端并发轮询 switchStatus 时，同一条记录可能被多个请求同时处理，
  // 导致 switch_count 重复 +1。先原子地把 status 从 pending 改成 processing，只有抢到的才回写 assignment。
  const claim = await env.DB.prepare("UPDATE switch_requests SET status = 'processing' WHERE id = ? AND status = 'pending'").bind(req.id).run();
  if (!(claim.meta && claim.meta.changes)) {
    return { resource_id: req.old_id, stage: "done", status: "done", new_ip: req.new_ip || newIp };
  }

  const row = await env.DB.prepare("SELECT * FROM assignments WHERE id = ?").bind(req.assignment_id).first();
  if (row) {
    let nodeData = {};
    try { nodeData = JSON.parse(row.node_data); } catch (_) {}
    nodeData.host = newIp;
    nodeData.ip = newIp;
    // 更新位置/地区（新实例的 region，客户端显示实时地区，与服务端一致）
    const newRegion = inst.region || "";
    if (newRegion) {
      nodeData.region = newRegion;
      const cityName = REGION_CITY[newRegion] || "";
      const countryName = COUNTRY_NAMES[REGION_COUNTRY[newRegion]] || "";
      if (countryName && cityName) nodeData.location = `${countryName}·${cityName}`;
    }
    const domain = nodeData.domain || "";
    // 补 links（防切换后分享链接丢失）：host 用域名（切 IP 域名不变），凭据不变，仅空时生成
    if (!nodeData.links || Object.keys(nodeData.links).length === 0) {
      const links = buildLinks(nodeData);
      if (links) nodeData.links = links;
    }
    // 注：DNS 已在「拿到新机 IP 后」提前更新（见上文），这里不再重复调用。
    await env.DB.prepare(
      "UPDATE assignments SET resource_id = ?, node_data = ? WHERE id = ?",
    ).bind(req.new_id, JSON.stringify(nodeData), row.id).run();
    await env.DB.prepare(
      "UPDATE assignments SET switch_count = ?, switch_date = ? WHERE id = ?",
    ).bind((row.switch_date || "") === bjToday() ? (row.switch_count || 0) + 1 : 1, bjToday(), row.id).run();
  }

  await env.DB.prepare("UPDATE switch_requests SET status = 'done', stage = 'done', new_ip = ? WHERE id = ?").bind(newIp, req.id).run();
  await notifySwitch(env, req.user_id, { resource_id: req.old_id, stage: "done", status: "done", new_ip: newIp });
  return { resource_id: req.old_id, stage: "done", status: "done", new_ip: newIp };
}

async function switchStatus(request, env, user) {
  const VULTR_KEY = env.VULTR_API_KEY || "";
  if (!VULTR_KEY) return json({ status: "none" });
  const headers = apiHeaders(env, "vultr");

  // 查该用户所有进行中的切换 + 最近 10 分钟已完成的切换。
  // 关键：done 的记录不再属于 pending，但并发切换时先完成的节点必须仍能被客户端匹配到（resource_id），
  // 否则客户端找不到自己那一台的 done 状态，会误判「切换失败」。所以 done 记录也要返回（带 stage=done）。
  const since = new Date(Date.now() - 10 * 60 * 1000).toISOString();
  const reqs = await env.DB.prepare(
    "SELECT * FROM switch_requests WHERE user_id = ? AND (status IN ('pending','processing') OR (status = 'done' AND created_at > ?)) ORDER BY id ASC",
  ).bind(user.id, since).all();
  const all = reqs.results || [];
  if (all.length === 0) return json({ status: "none" });

  const items = [];
  for (const req of all) {
    // done 或 processing（已被并发请求抢占、即将完成）都按完成返回
    if (req.status === "done" || req.status === "processing") {
      items.push({ resource_id: req.old_id, stage: "done", status: "done", new_ip: req.new_ip || "" });
    } else {
      items.push(await processOneSwitch(req, env, headers, request));
    }
  }
  const anyPending = items.some((it) => it.status === "pending");
  // 兜底收尾：即使 Cron 未触发，客户端每次轮询也顺手收尾所有超时 10 分钟的 pending 记录（含其他用户的卡住切换）。
  // sweepPending 有「超时 10 分钟」限制 + 抢占保护，重复调用安全。
  try {
    await sweepPending(env);
  } catch (_) {}
  return json({ status: anyPending ? "pending" : "done", items });
}


async function adminSwitch(request, env) {
  const body = await request.json().catch(() => ({}));
  const resourceId = (body.resource_id || "").trim();
  if (!resourceId) return json({ error: "resource_id 必填" }, 400);
  const domain = (body.domain || "").trim();

  const VULTR_KEY = env.VULTR_API_KEY || "";
  if (!VULTR_KEY) return json({ error: "服务端未配置 Vultr API key" }, 500);
  let creds = {};
  if (body.creds && typeof body.creds === "object" && Object.keys(body.creds).length > 0) {
    creds = body.creds;  // 管理端传来的该服务器独立凭据
  } else {
    // 优先沿用原节点的独立凭据（node_data._creds），否则 fallback GLOBAL_CREDS。
    // 之前直接用 GLOBAL_CREDS，导致管理端切换后凭据被全局凭据覆盖、客户端认证失败（切换参数「变了」）。
    const oldAssign = await env.DB.prepare(
      "SELECT node_data FROM assignments WHERE resource_type = 'vultr' AND resource_id = ?",
    ).bind(resourceId).first();
    if (oldAssign) {
      try {
        const oldNd = JSON.parse(oldAssign.node_data);
        if (oldNd._creds && typeof oldNd._creds === "object" && Object.keys(oldNd._creds).length > 0) {
          creds = oldNd._creds;
        }
      } catch (_) {}
    }
    if (!creds || Object.keys(creds).length === 0) {
      try { creds = JSON.parse(env.GLOBAL_CREDS || "{}"); } catch (_) {}
    }
  }

  const headers = apiHeaders(env, "vultr");

  const oldResp = await fetch(`${apiBase(env, "vultr")}/v2/instances/${resourceId}`, { headers });
  if (!oldResp.ok) {
    return json({ error: `查询旧实例失败 HTTP ${oldResp.status}` }, 500);
  }
  const oldInst = ((await oldResp.json()).instance) || {};
  const oldRegion = oldInst.region || "";
  // 套餐：管理员可显式指定（body.plan），未指定则沿用旧机套餐。
  // 客户端 switchResource 不支持指定 plan（保持旧套餐），只有服务端能换套餐。
  const plan = (body.plan || "").trim() || oldInst.plan || "";
  const label = oldInst.label || "vultr-proxy-node";
  const country = REGION_COUNTRY[oldRegion] || "";
  const targetRegion = (body.region || "").trim();

  // 换套餐（管理员显式指定不同 plan）时，查新套餐带宽，切换完成后自动同步客户配额
  let newBandwidth = 0;
  if ((body.plan || "").trim() && (body.plan || "").trim() !== (oldInst.plan || "")) {
    try {
      const planResp = await fetch(`${apiBase(env, "vultr")}/v2/plans/${plan}`, { headers });
      if (planResp.ok) {
        const planData = await planResp.json();
        newBandwidth = Number((planData.plan && planData.plan.bandwidth) || 0);
      }
    } catch (_) {}
  }

  let newRegion = targetRegion;
  if (!newRegion) {
    let candidates = Object.keys(REGION_COUNTRY).filter((r) => r !== oldRegion && REGION_COUNTRY[r] === country);
    if (candidates.length === 0) candidates = Object.keys(REGION_COUNTRY).filter((r) => REGION_COUNTRY[r] === country);
    newRegion = candidates.length ? candidates[Math.floor(Math.random() * candidates.length)] : oldRegion;
  }

  // 先创建新机，成功后再销毁旧机（避免创建失败导致旧机已销毁、节点丢失）
  const cert = await getCachedCert(env, domain);
  const workerUrl = new URL(request.url).origin;
  const certToken = env.CERT_TOKEN || "";
  const userData = buildGostUserData(creds, domain, cert, workerUrl, certToken);
  const createResp = await fetch(apiBase(env, "vultr") + "/v2/instances", {
    method: "POST",
    headers,
    body: JSON.stringify({ region: newRegion, plan, os_id: 1743, label, user_data: userData, backups: "disabled" }),
  });
  if (!createResp.ok) {
    const errText = await createResp.text().catch(() => "");
    return json({ error: `创建新机失败 HTTP ${createResp.status}${errText ? "：" + errText.slice(0, 200) : ""}` }, 500);
  }
  let newInst = {};
  try {
    newInst = ((await createResp.json()).instance) || {};
  } catch (_) {
    return json({ error: "创建新机响应异常，请重试" }, 500);
  }
  const newId = newInst.id || "";
  if (!newId) {
    return json({ error: "创建新机失败：未返回实例 ID" }, 500);
  }

  // 销毁旧机前：抓旧机出站流量（Vultr 不保留已销毁实例的流量明细，必须先抓再销毁）
  let oldUsedGb = 0;
  try {
    const bwResp = await fetch(`${apiBase(env, "vultr")}/v2/instances/${resourceId}/bandwidth`, { headers });
    if (bwResp.ok) {
      const bwData = await bwResp.json();
      const bw = bwData.bandwidth || {};
      oldUsedGb = Object.values(bw).reduce((s, d) => s + (Number(d.outgoing_bytes) || 0), 0) / 1e9;
    }
  } catch (_) {}

  const delResp = await fetch(`${apiBase(env, "vultr")}/v2/instances/${resourceId}`, { method: "DELETE", headers });
  // 销毁旧机失败不阻断切换（新机已创建，返回错误会让客户端重试、重复创建新机造成更多孤儿）。
  // 记日志，旧机可后续由巡检/手动处理，避免「销毁失败→重试→孤儿新机」雪崩。
  if (!delResp.ok && delResp.status !== 404) {
    console.error(`销毁旧机 ${resourceId} 失败 HTTP ${delResp.status}（不阻断切换，新机已创建）`);
  }

  await env.DB.prepare(
    "INSERT INTO switch_requests (assignment_id, user_id, old_id, new_id, status, stage, actor_type, domain, created_at, old_used_gb, new_bandwidth) VALUES (0, 0, ?, ?, 'pending', 'booting', 'admin', ?, ?, ?, ?)",
  ).bind(resourceId, newId, domain, new Date().toISOString(), oldUsedGb, newBandwidth).run();

  return json({ ok: true, status: "pending", old_id: resourceId, new_id: newId });
}

async function processAdminSwitch(req, env, headers, request) {
  const resp = await fetch(`${apiBase(env, "vultr")}/v2/instances/${req.new_id}`, { headers });
  if (!resp.ok) {
    // 新机 404 且超过 10 分钟 = 已被销毁，标记 failed 避免永久卡 pending
    if (resp.status === 404) {
      const runningMs = Date.now() - new Date(req.created_at || Date.now()).getTime();
      if (runningMs > 600000) {
        await env.DB.prepare("UPDATE switch_requests SET status = 'failed', stage = 'failed' WHERE id = ?").bind(req.id).run();
        return { old_id: req.old_id, stage: "failed", status: "failed", note: "新机已不存在（可能被后续切换销毁）" };
      }
    }
    return { old_id: req.old_id, stage: "booting", status: "pending", note: "查询新机状态失败" };
  }
  const inst = ((await resp.json()).instance) || {};
  const power = inst.power_status || inst.status || "";
  const newIp = inst.main_ip || "";
  if (power !== "running" || !newIp) {
    return { old_id: req.old_id, stage: "booting", status: "pending", note: "新机已创建，正在启动中" };
  }

  // 拿到新机 IP 后立即更新 DNS（不等 cloud-init），让域名解析尽早生效。
  // 核心卖点：客户设一次域名，切 IP 不用改配置。用 new_ip 做「已更新」标记避免重复调用。
  if (!req.new_ip && req.domain) {
    await updateDns(env, req.domain, newIp);
    await env.DB.prepare("UPDATE switch_requests SET new_ip = ? WHERE id = ?").bind(newIp, req.id).run();
  }

  // 真实检测安装完成：等新机 cloud-init 回写 node-ready（stage 变 'installed'），不死等固定时长。
  // 超时 5 分钟兜底（cloud-init 异常没回写时按就绪处理，客户端真实检测端口，不通自动重切）。
  if (req.stage !== "installed") {
    const runningMs = Date.now() - new Date(req.created_at || Date.now()).getTime();
    if (runningMs < 300000) {
      return { old_id: req.old_id, stage: "installing", status: "pending", note: "新机运行中，正在下载安装代理软件（真实等待安装完成）" };
    }
  }

  // 抢占（防并发重复处理）：服务端多台并发切换时，同一条记录可能被多个请求同时处理，先原子抢占。
  const claim = await env.DB.prepare("UPDATE switch_requests SET status = 'processing' WHERE id = ? AND status = 'pending'").bind(req.id).run();
  if (!(claim.meta && claim.meta.changes)) {
    return { old_id: req.old_id, stage: "done", status: "done", new_ip: req.new_ip || newIp };
  }

  // 回写：旧实例若已分配给某用户，更新 assignment 的 resource_id + node_data（客户端同步）
  const assign = await env.DB.prepare(
    "SELECT * FROM assignments WHERE resource_type = 'vultr' AND resource_id = ?",
  ).bind(req.old_id).first();
  let domain = "";
  if (assign) {
    let nodeData = {};
    try { nodeData = JSON.parse(assign.node_data); } catch (_) {}
    nodeData.host = newIp;
    nodeData.ip = newIp;
    // 更新位置/地区（新实例的 region，客户端显示实时地区，与服务端一致）
    const newRegion = inst.region || "";
    if (newRegion) {
      nodeData.region = newRegion;
      const cityName = REGION_CITY[newRegion] || "";
      const countryName = COUNTRY_NAMES[REGION_COUNTRY[newRegion]] || "";
      if (countryName && cityName) nodeData.location = `${countryName}·${cityName}`;
    }
    domain = nodeData.domain || "";
    // 补 links（防切换后分享链接丢失）：host 用域名（切 IP 域名不变），凭据不变，仅空时生成
    if (!nodeData.links || Object.keys(nodeData.links).length === 0) {
      const links = buildLinks(nodeData);
      if (links) nodeData.links = links;
    }
    // 换套餐：同步客户配额到新套餐带宽（node_data.quota_gb + assignments.quota_gb，限流依据）
    if (Number(req.new_bandwidth) > 0) {
      nodeData.quota_gb = Number(req.new_bandwidth);
    }
    if (domain) await updateDns(env, domain, newIp);
    await env.DB.prepare(
      "UPDATE assignments SET resource_id = ?, node_data = ?, quota_gb = ? WHERE id = ?",
    ).bind(req.new_id, JSON.stringify(nodeData), Number(req.new_bandwidth) > 0 ? Number(req.new_bandwidth) : Number(assign.quota_gb) || 0, assign.id).run();
  } else if (req.domain) {
    domain = req.domain;
    await updateDns(env, req.domain, newIp);
  }

  await env.DB.prepare("UPDATE switch_requests SET status = 'done', stage = 'done', new_ip = ? WHERE id = ?").bind(newIp, req.id).run();
  return { old_id: req.old_id, stage: "done", status: "done", new_ip: newIp, domain, label: inst.label || "", region: inst.region || "" };
}

async function adminSwitchStatus(request, env) {
  const VULTR_KEY = env.VULTR_API_KEY || "";
  if (!VULTR_KEY) return json({ status: "none" });
  const headers = apiHeaders(env, "vultr");

  // 查所有 admin 进行中的切换 + 最近 10 分钟已完成的（并发切换时先完成的仍能被匹配到，避免误判失败）
  const since = new Date(Date.now() - 10 * 60 * 1000).toISOString();
  const reqs = await env.DB.prepare(
    "SELECT * FROM switch_requests WHERE actor_type = 'admin' AND (status IN ('pending','processing') OR (status = 'done' AND created_at > ?)) ORDER BY id ASC",
  ).bind(since).all();
  const all = reqs.results || [];
  if (all.length === 0) return json({ status: "none" });

  const items = [];
  for (const req of all) {
    if (req.status === "done") {
      items.push({ old_id: req.old_id, stage: "done", status: "done", new_ip: req.new_ip || "", domain: req.domain || "" });
    } else {
      items.push(await processAdminSwitch(req, env, headers, request));
    }
  }
  const anyPending = items.some((it) => it.status === "pending");
  // 兜底收尾：服务端轮询也顺手收尾所有超时 10 分钟的 pending 记录
  try {
    await sweepPending(env);
  } catch (_) {}
  return json({ status: anyPending ? "pending" : "done", items });
}

// ============ 管理员 handlers ============

async function listUsers(env) {
  const users = await env.DB.prepare("SELECT * FROM users ORDER BY id DESC").all();
  const assigns = await env.DB.prepare(
    "SELECT * FROM assignments ORDER BY created_at DESC",
  ).all();
  const byUser = {};
  for (const a of assigns.results) {
    if (!byUser[a.user_id]) byUser[a.user_id] = [];
    byUser[a.user_id].push(a);
  }

  const result = users.results.map((u) => {
    const list = (byUser[u.id] || []).map((a) => {
      let data = {};
      try { data = JSON.parse(a.node_data); } catch (_) { data = {}; }
      return {
        resource_type: a.resource_type,
        resource_id: a.resource_id,
        node_data: data,
        created_at: a.created_at,
      };
    });
    return {
      id: u.id,
      username: u.username,
      status: u.status,
      expires_at: u.expires_at,
      note: u.note,
      created_at: u.created_at,
      updated_at: u.updated_at,
      switch_limit: u.switch_limit,
      switch_count: u.switch_count,
      switch_date: u.switch_date,
      assignment_count: list.length,
      assignments: list,
    };
  });
  return json({ users: result, total: result.length });
}

async function createUser(request, env) {
  const body = await request.json().catch(() => ({}));
  const username = String(body.username || "").trim();
  const password = String(body.password || "");
  if (!username || !password) return json({ error: "用户名和密码不能为空" }, 400);
  if (username.length < 2 || username.length > 64) {
    return json({ error: "用户名长度需在 2-64 之间" }, 400);
  }
  if (password.length < 6) return json({ error: "密码至少 6 位" }, 400);

  const existing = await env.DB.prepare("SELECT id FROM users WHERE username = ?")
    .bind(username)
    .first();
  if (existing) return json({ error: "用户名已存在" }, 409);

  const hash = await hashPassword(password);
  const now = new Date().toISOString();
  const status = body.status === "disabled" ? "disabled" : "active";
  const expiresAt = body.expires_at ? String(body.expires_at) : null;
  const note = String(body.note || "");
  const switchLimit = body.switch_limit === undefined ? 5 : Math.max(0, parseInt(body.switch_limit, 10) || 0);

  const result = await env.DB.prepare(
    "INSERT INTO users (username, password_hash, status, expires_at, note, switch_limit, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
  )
    .bind(username, hash, status, expiresAt, note, switchLimit, now, now)
    .run();

  return json({ id: result.meta.last_row_id, username }, 201);
}

async function getUser(env, id) {
  const user = await env.DB.prepare("SELECT * FROM users WHERE id = ?").bind(id).first();
  if (!user) return json({ error: "用户不存在" }, 404);

  const assigns = await env.DB.prepare(
    "SELECT * FROM assignments WHERE user_id = ? ORDER BY id",
  )
    .bind(id)
    .all();
  const assignments = assigns.results.map((a) => {
    let data = {};
    try {
      data = JSON.parse(a.node_data);
    } catch (_) {
      data = {};
    }
    return {
      id: a.id,
      resource_type: a.resource_type,
      resource_id: a.resource_id,
      node_data: data,
      created_at: a.created_at,
      switch_count: a.switch_count || 0,
      switch_date: a.switch_date || "",
    };
  });

  return json({
    user: {
      id: user.id,
      username: user.username,
      status: user.status,
      expires_at: user.expires_at,
      note: user.note,
      switch_limit: user.switch_limit,
      switch_count: user.switch_count,
      switch_date: user.switch_date,
      created_at: user.created_at,
      updated_at: user.updated_at,
    },
    assignments,
  });
}

async function updateUser(request, env, id) {
  const body = await request.json().catch(() => ({}));
  const existing = await env.DB.prepare("SELECT * FROM users WHERE id = ?").bind(id).first();
  if (!existing) return json({ error: "用户不存在" }, 404);

  const now = new Date().toISOString();
  let passwordHash = existing.password_hash;
  if (body.password) {
    if (String(body.password).length < 6) return json({ error: "密码至少 6 位" }, 400);
    passwordHash = await hashPassword(String(body.password));
  }
  const status =
    body.status === "disabled" ? "disabled" : body.status === "active" ? "active" : existing.status;
  const expiresAt =
    body.expires_at === undefined
      ? existing.expires_at
      : body.expires_at
        ? String(body.expires_at)
        : null;
  const note = body.note === undefined ? existing.note : String(body.note || "");
  const switchLimit =
    body.switch_limit === undefined
      ? existing.switch_limit
      : Math.max(0, parseInt(body.switch_limit, 10) || 0);

  await env.DB.prepare(
    "UPDATE users SET password_hash = ?, status = ?, expires_at = ?, note = ?, switch_limit = ?, updated_at = ? WHERE id = ?",
  )
    .bind(passwordHash, status, expiresAt, note, switchLimit, now, id)
    .run();

  // 每台服务器独立的切换次数上限：传了 resource_id + switch_limit 时同步到该节点（客户端限流依据）
  if (body.resource_id && body.switch_limit !== undefined) {
    await env.DB.prepare(
      "UPDATE assignments SET switch_limit = ? WHERE user_id = ? AND resource_id = ?",
    )
      .bind(switchLimit, id, String(body.resource_id))
      .run();
  }

  // 重置切换次数：可指定 resource_id 只重置某节点，否则重置该用户全部
  if (body.reset_switch) {
    const today = bjToday();
    if (body.resource_id) {
      await env.DB.prepare(
        "UPDATE assignments SET switch_count = 0, switch_date = ? WHERE user_id = ? AND resource_id = ?",
      )
        .bind(today, id, body.resource_id)
        .run();
    } else {
      await env.DB.prepare(
        "UPDATE assignments SET switch_count = 0, switch_date = ? WHERE user_id = ?",
      )
        .bind(today, id)
        .run();
    }
  }

  return json({ ok: true });
}

async function deleteUser(env, id) {
  const existing = await env.DB.prepare("SELECT id FROM users WHERE id = ?").bind(id).first();
  if (!existing) return json({ error: "用户不存在" }, 404);
  // 外键 ON DELETE CASCADE 自动清理 assignments + sessions
  await env.DB.prepare("DELETE FROM users WHERE id = ?").bind(id).run();
  return json({ ok: true });
}

async function assignResource(request, env) {
  const body = await request.json().catch(() => ({}));
  const userId = parseInt(body.user_id, 10);
  const resourceType = String(body.resource_type || "");
  const resourceId = String(body.resource_id || "");
  const nodeData = body.node_data || {};
  // 客户到期由 node_data.client_expiry 管理（每台服务器独立），不再写用户级 users.expires_at
  // 区分「显式传了 quota_gb（含 0=不限制）」与「没传（保留原配额）」
  const hasQuota = body.quota_gb !== undefined && body.quota_gb !== null;
  const quotaGb = hasQuota ? Number(body.quota_gb) || 0 : null;

  if (!userId) return json({ error: "user_id 必填" }, 400);
  if (resourceType !== "vultr") {
    return json({ error: "resource_type 必须是 vultr" }, 400);
  }
  if (!resourceId) return json({ error: "resource_id 必填" }, 400);

  const user = await env.DB.prepare("SELECT id FROM users WHERE id = ?").bind(userId).first();
  if (!user) return json({ error: "用户不存在" }, 404);

  const nodeDataJson = typeof nodeData === "string" ? nodeData : JSON.stringify(nodeData);
  if (hasQuota) {
    await env.DB.prepare(
      "INSERT INTO assignments (user_id, resource_type, resource_id, node_data, created_at, quota_gb) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(user_id, resource_type, resource_id) DO UPDATE SET node_data = excluded.node_data, quota_gb = excluded.quota_gb",
    )
      .bind(userId, resourceType, resourceId, nodeDataJson, new Date().toISOString(), quotaGb)
      .run();
  } else {
    await env.DB.prepare(
      "INSERT INTO assignments (user_id, resource_type, resource_id, node_data, created_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id, resource_type, resource_id) DO UPDATE SET node_data = excluded.node_data",
    )
      .bind(userId, resourceType, resourceId, nodeDataJson, new Date().toISOString())
      .run();
  }

  return json({ ok: true }, 201);
}

async function syncFlow(request, env) {
  // 管理端批量同步每台服务器的累计已用流量（GB）到 D1 的 assignments.used_gb。
  // body: { updates: [{ resource_id, used_gb }, ...] }
  const body = await request.json().catch(() => ({}));
  const updates = Array.isArray(body.updates) ? body.updates : [];
  if (updates.length === 0) return json({ ok: true, updated: 0 });

  const stmt = env.DB.prepare("UPDATE assignments SET used_gb = ? WHERE resource_id = ?");
  const batch = updates.map((u) =>
    stmt.bind(Number(u.used_gb) || 0, String(u.resource_id || ""))
  );
  const results = await env.DB.batch(batch);
  let updated = 0;
  for (const r of results) {
    if (r && r.meta && r.meta.changes) updated += r.meta.changes;
  }
  return json({ ok: true, updated });
}

async function resetFlow(request, env) {
  // 重置该服务器的已用流量（used_gb = 0），客户续费时重新给满配额。
  // body: { resource_id }
  const body = await request.json().catch(() => ({}));
  const resourceId = String(body.resource_id || "").trim();
  if (!resourceId) return json({ error: "resource_id 必填" }, 400);
  const result = await env.DB.prepare(
    "UPDATE assignments SET used_gb = 0 WHERE resource_id = ?",
  ).bind(resourceId).run();
  return json({ ok: true, updated: (result.meta && result.meta.changes) || 0 });
}

async function switchFlow(env) {
  // 返回切换记录（含 pending），供服务端按 old_id → new_id 链追踪迁移配置。
  // 含 pending 是因为客户端切换后可能没轮询完成，但新机已 running，配置仍需迁移。
  const rows = await env.DB.prepare(
    "SELECT old_id, new_id, old_used_gb, status, actor_type, created_at FROM switch_requests ORDER BY id DESC LIMIT 200",
  ).all();
  return json({ switches: rows.results });
}

async function unassignResource(request, env) {
  const body = await request.json().catch(() => ({}));
  const userId = parseInt(body.user_id, 10);
  const resourceType = String(body.resource_type || "");
  const resourceId = String(body.resource_id || "");
  if (!userId || !resourceType || !resourceId) return json({ error: "参数不完整" }, 400);

  await env.DB.prepare(
    "DELETE FROM assignments WHERE user_id = ? AND resource_type = ? AND resource_id = ?",
  )
    .bind(userId, resourceType, resourceId)
    .run();

  return json({ ok: true });
}

async function listResourceAssignments(env, resourceType, resourceId) {
  const rows = await env.DB.prepare(
    "SELECT a.user_id, a.created_at, u.username, u.note, u.status FROM assignments a JOIN users u ON u.id = a.user_id WHERE a.resource_type = ? AND a.resource_id = ? ORDER BY a.created_at DESC",
  )
    .bind(resourceType, resourceId)
    .all();
  return json({
    resource_type: resourceType,
    resource_id: resourceId,
    total: rows.results.length,
    assignments: rows.results,
  });
}

async function listAllAssignments(env) {
  const rows = await env.DB.prepare(
    "SELECT a.id, a.user_id, a.resource_type, a.resource_id, a.node_data, a.created_at, a.switch_limit AS node_switch_limit, a.switch_count AS node_switch_count, a.switch_date AS node_switch_date, u.username, u.status, u.note, u.expires_at AS user_expires_at, u.switch_limit AS user_switch_limit FROM assignments a JOIN users u ON u.id = a.user_id ORDER BY a.created_at DESC",
  ).all();
  const assignments = rows.results.map((a) => {
    let data = {};
    try { data = JSON.parse(a.node_data); } catch (_) { data = {}; }
    return {
      id: a.id,
      user_id: a.user_id,
      username: a.username,
      user_status: a.status,
      user_note: a.note,
      user_expires_at: a.user_expires_at,
      user_switch_limit: a.user_switch_limit,
      node_switch_limit: a.node_switch_limit,
      node_switch_count: a.node_switch_count,
      node_switch_date: a.node_switch_date,
      resource_type: a.resource_type,
      resource_id: a.resource_id,
      node_data: data,
      created_at: a.created_at,
    };
  });
  return json({ assignments, total: assignments.length });
}

async function getStats(env) {
  const users = await env.DB.prepare("SELECT COUNT(*) AS cnt FROM users").first();
  const active = await env.DB.prepare(
    "SELECT COUNT(*) AS cnt FROM users WHERE status = 'active'",
  ).first();
  const assigns = await env.DB.prepare("SELECT COUNT(*) AS cnt FROM assignments").first();
  const assignedUsers = await env.DB.prepare(
    "SELECT COUNT(DISTINCT user_id) AS cnt FROM assignments",
  ).first();
  const byType = await env.DB.prepare(
    "SELECT resource_type, COUNT(*) AS cnt FROM assignments GROUP BY resource_type",
  ).all();

  return json({
    total_users: users.cnt,
    active_users: active.cnt,
    total_assignments: assigns.cnt,
    assigned_users: assignedUsers.cnt,
    by_type: byType.results,
  });
}

// ============ 路由 ============

// 定时收尾：处理卡住超过 10 分钟的 pending 切换记录（客户端轮询超时放弃后，这里兜底自愈）。
// 复用 processOneSwitch：查新机状态 → 回写 assignment → 标记 done，避免 assignment 卡在已销毁旧机导致后续切换 404。
async function sweepPending(env) {
  const VULTR_KEY = env.VULTR_API_KEY || "";
  if (!VULTR_KEY) return;
  const headers = apiHeaders(env, "vultr");
  const cutoff = new Date(Date.now() - 600000).toISOString();
  const rows = await env.DB.prepare(
    "SELECT * FROM switch_requests WHERE status IN ('pending','processing') AND created_at < ? ORDER BY id ASC",
  ).bind(cutoff).all();
  for (const req of rows.results) {
    try {
      // 管理端切换（actor_type=admin，assignment_id=0）按 resource_id 回写，客户端切换按 assignment_id 回写
      if (req.actor_type === "admin") {
        await processAdminSwitch(req, env, headers, null);
      } else {
        await processOneSwitch(req, env, headers, null);
      }
    } catch (e) {
      console.error("sweepPending 收尾失败:", req.id, e && e.message ? e.message : e);
    }
  }
}

export default {
  SwitchRoom, // Durable Object 类（WebSocket 实时推送）
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS });
    }

    const url = new URL(request.url);
    const path = url.pathname;
    const method = request.method;

    try {
      // 前端静态资源（热更新：改前端重新部署 Worker 即可，客户端远程加载）
      if (method === "GET") {
        if (path === "/" || path === "/app" || path === "/index.html") {
          return new Response(indexHtml, { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store, no-cache, must-revalidate", ...CORS } });
        }
        if (path === "/main.js") {
          return new Response(mainJs, { headers: { "Content-Type": "application/javascript; charset=utf-8", "Cache-Control": "no-store, no-cache, must-revalidate", ...CORS } });
        }
        if (path === "/style.css") {
          return new Response(styleCss, { headers: { "Content-Type": "text/css; charset=utf-8", "Cache-Control": "no-store, no-cache, must-revalidate", ...CORS } });
        }
      }

      // 客户端版本检查（无需登录）
      if (method === "GET" && path === "/version") {
        return json({ version: CLIENT_VERSION, download_url: CLIENT_DOWNLOAD_URL, note: CLIENT_RELEASE_NOTE });
      }

      // 前端构建时间戳（客户端轮询检测热更新）
      if (method === "GET" && path === "/build-stamp") {
        return json({ stamp: FRONTEND_STAMP });
      }

      // WebSocket：切换进度实时推送（路由到该用户的 SwitchRoom DO）
      // 浏览器 WebSocket 握手不能自定义 Authorization 头，token 走 query 参数（?token=xxx）
      if (path === "/ws") {
        let user = null;
        const wsToken = (url.searchParams.get("token") || "").trim();
        if (wsToken) {
          const row = await env.DB.prepare(
            "SELECT s.user_id AS id, u.username, u.status FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ? AND s.expires_at > ?",
          ).bind(wsToken, new Date().toISOString()).first();
          if (row && row.status === "active") user = { id: row.id, username: row.username };
        } else {
          user = await requireUser(request, env); // 非浏览器客户端（可带 Authorization 头）
        }
        if (!user) return json({ error: "未登录或登录已过期" }, 401);
        const id = env.SWITCH_ROOM.idFromName("u" + user.id);
        return await env.SWITCH_ROOM.get(id).fetch(request);
      }

      // 客户端端点
      if (method === "POST" && path === "/login") return await handleLogin(request, env);
      if (method === "POST" && path === "/me/switch") {
        const user = await requireUser(request, env);
        if (!user) return json({ error: "未登录或登录已过期" }, 401);
        return await switchResource(request, env, user);
      }
      if (method === "GET" && path === "/me/switch/status") {
        const user = await requireUser(request, env);
        if (!user) return json({ error: "未登录或登录已过期" }, 401);
        return await switchStatus(request, env, user);
      }
      if (method === "GET" && path === "/me/nodes") {
        const user = await requireUser(request, env);
        if (!user) return json({ error: "未登录或登录已过期" }, 401);
        return await handleMeNodes(env, user);
      }
      if (method === "POST" && path === "/me/account") {
        const user = await requireUser(request, env);
        if (!user) return json({ error: "未登录或登录已过期" }, 401);
        return await handleMeAccount(request, env, user);
      }
      if (method === "POST" && path === "/me/note") {
        const user = await requireUser(request, env);
        if (!user) return json({ error: "未登录或登录已过期" }, 401);
        return await handleMeNote(request, env, user);
      }
      if (method === "POST" && path === "/me/reorder") {
        const user = await requireUser(request, env);
        if (!user) return json({ error: "未登录或登录已过期" }, 401);
        return await handleMeReorder(request, env, user);
      }
      // 节点签好证书后回传（用 X-Cert-Token 认证，非管理员 token）
      if (method === "POST" && path === "/admin/cert-save") return await certSave(request, env);

      // 新机 cloud-init 安装完代理软件后回写（管理员认证之前，cloud-init 不带 token）：
      // 把 switch_requests.stage 置为 installed，Worker 轮询据此判定「安装完成」，实现真实分阶段回显。
      if (method === "GET" && path === "/admin/node-ready") {
        const newId = (url.searchParams.get("new_id") || "").trim();
        if (!newId) return json({ error: "new_id 必填" }, 400);
        await env.DB.prepare("UPDATE switch_requests SET stage = 'installed' WHERE new_id = ? AND status = 'pending'").bind(newId).run();
        return json({ ok: true });
      }

      // 管理员端点
      if (!(await requireAdmin(request, env))) {
        return json({ error: "管理员认证失败" }, 401);
      }

      if (path === "/admin/users" && method === "GET") return await listUsers(env);
      if (path === "/admin/users" && method === "POST") return await createUser(request, env);
      if (path === "/admin/stats" && method === "GET") return await getStats(env);

      const userMatch = path.match(/^\/admin\/users\/(\d+)$/);
      if (userMatch) {
        const id = parseInt(userMatch[1], 10);
        if (method === "GET") return await getUser(env, id);
        if (method === "PUT") return await updateUser(request, env, id);
        if (method === "DELETE") return await deleteUser(env, id);
      }

      if (path === "/admin/assignments" && method === "POST") {
        return await assignResource(request, env);
      }
      if (path === "/admin/assignments" && method === "DELETE") {
        return await unassignResource(request, env);
      }

      if (path === "/admin/switch" && method === "POST") return await adminSwitch(request, env);
      if (path === "/admin/switch/status" && method === "GET") return await adminSwitchStatus(request, env);
      if (path === "/admin/all-assignments" && method === "GET") return await listAllAssignments(env);
      if (path === "/admin/sync-flow" && method === "POST") return await syncFlow(request, env);
      if (path === "/admin/reset-flow" && method === "POST") return await resetFlow(request, env);
      if (path === "/admin/switch-flow" && method === "GET") return await switchFlow(env);

      if (path === "/admin/resource-assignments" && method === "GET") {
        const rt = url.searchParams.get("resource_type") || "";
        const rid = url.searchParams.get("resource_id") || "";
        if (!rt || !rid) return json({ error: "resource_type 和 resource_id 必填" }, 400);
        return await listResourceAssignments(env, rt, rid);
      }

      return json({ error: "Not Found" }, 404);
    } catch (e) {
      // 不把 e.message 回传客户端（会泄露 D1 表名/列名、fetch URL 等内部结构），记日志 + 固定文案
      console.error("Unhandled error:", e && e.message ? e.message : e);
      return json({ error: "服务器内部错误" }, 500);
    }
  },
  // Cloudflare Cron Trigger：每 10 分钟收尾卡住的切换记录（wrangler.toml triggers.crons）
  async scheduled(_event, env) {
    try {
      console.log(`[scheduled] 触发收尾，VULTR_API_KEY=${env.VULTR_API_KEY ? "有" : "空"}`);
      await sweepPending(env);
    } catch (e) {
      console.error("scheduled sweepPending 异常:", e && e.message ? e.message : e);
    }
  },
};

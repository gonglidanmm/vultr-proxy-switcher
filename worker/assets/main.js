const API = "https://user.yduas.cc";
const TOKEN_KEY = "proxy_client_token";
const USER_KEY = "proxy_client_user";

let token = localStorage.getItem(TOKEN_KEY) || "";
let user = null;
try { user = JSON.parse(localStorage.getItem(USER_KEY) || "null"); } catch (_) { user = null; }

const $ = (id) => document.getElementById(id);

// ===== 复制（带反馈 + fallback） =====
async function copyText(text, btn) {
  const orig = btn.textContent;
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
    btn.textContent = "已复制 ✓";
    btn.classList.add("copied");
  } catch (_) {
    btn.textContent = "复制失败";
  }
  setTimeout(() => { btn.textContent = orig; btn.classList.remove("copied"); }, 1500);
}

// 复制节点弹窗（复刻服务端分享：分组 + 完整链接 + 手动字段逐个复制）
function openShareDialog(n) {
  $("share-node-name").textContent = n.name || n.host || "节点";
  const body = $("share-body");
  body.innerHTML = "";
  const host = n.host || n.domain || "";

  const groups = [
    {
      name: "SOCKS5",
      desc: "标准 SOCKS5 代理，带用户名密码认证",
      link: n.links?.socks5,
      fields: [
        ["地址", host, "服务器地址（域名）"],
        ["端口", n.socks5?.port, "SOCKS5 代理端口"],
        ["用户名", n.socks5?.username, "认证用户名"],
        ["密码", n.socks5?.password, "认证密码"],
      ],
    },
    {
      name: "Shadowsocks",
      desc: "SS 加密代理",
      link: n.links?.ss,
      fields: [
        ["地址", host, "服务器地址（域名）"],
        ["端口", n.ss?.port, "SS 代理端口"],
        ["加密", n.ss?.method, "加密方式"],
        ["密码", n.ss?.password, "认证密码"],
      ],
    },
    {
      name: "Reality",
      desc: "VLESS + Reality（伪装 TLS，无需域名证书）",
      link: n.links?.reality,
      fields: [
        ["地址", host, "服务器地址（域名）"],
        ["端口", n.reality?.port, "Reality 端口（443）"],
        ["UUID", n.reality?.uuid, "用户 ID"],
        ["公钥", n.reality?.public_key, "Public Key（服务器公钥）"],
        ["Short ID", n.reality?.short_id, "短 ID（防盗用）"],
        ["SNI", n.reality?.sni, "伪装目标域名"],
        ["Flow", n.reality?.flow, "流控（xtls-rprx-vision）"],
        ["指纹", n.reality?.fp, "浏览器指纹（chrome）"],
      ],
    },
    {
      name: "Hysteria2",
      desc: "Hysteria2 高性能 QUIC 代理",
      link: n.links?.hysteria2,
      fields: [
        ["地址", host, "服务器地址（域名）"],
        ["端口", n.hysteria2?.port, "Hysteria2 端口"],
        ["密码", n.hysteria2?.password, "认证密码"],
        ["端口跳跃", n.hysteria2?.mport, "Port Hopping 端口范围"],
      ],
    },
  ];

  groups.forEach((g) => {
    const sec = document.createElement("div");
    sec.className = "share-group";

    const head = document.createElement("div");
    head.className = "share-group-head";
    const title = document.createElement("div");
    title.className = "share-group-title";
    title.textContent = g.name;
    head.appendChild(title);
    if (g.desc) {
      const d = document.createElement("div");
      d.className = "share-group-desc";
      d.textContent = g.desc;
      head.appendChild(d);
    }
    sec.appendChild(head);

    if (g.link) {
      const box = document.createElement("div");
      box.className = "share-link-box";
      const code = document.createElement("code");
      code.className = "share-link-val";
      code.textContent = g.link;
      const copyBtn = document.createElement("button");
      copyBtn.className = "btn";
      copyBtn.textContent = "复制链接";
      copyBtn.addEventListener("click", () => copyText(String(g.link), copyBtn));
      box.appendChild(code);
      box.appendChild(copyBtn);
      sec.appendChild(box);
    }

    g.fields.forEach(([label, val, desc]) => {
      if (val) sec.appendChild(buildFieldRow(label, val, desc));
    });
    body.appendChild(sec);
  });

  $("share-modal").style.display = "flex";
}

function buildFieldRow(label, val, desc) {
  const row = document.createElement("div");
  row.className = "share-row";
  const lblWrap = document.createElement("div");
  lblWrap.className = "share-label-wrap";
  const lbl = document.createElement("span");
  lbl.className = "share-label";
  lbl.textContent = label;
  lblWrap.appendChild(lbl);
  if (desc) {
    const d = document.createElement("span");
    d.className = "share-label-desc";
    d.textContent = desc;
    lblWrap.appendChild(d);
  }
  const valEl = document.createElement("code");
  valEl.className = "share-val";
  valEl.textContent = String(val);
  const copyBtn = document.createElement("button");
  copyBtn.className = "btn";
  copyBtn.textContent = "复制";
  copyBtn.addEventListener("click", () => copyText(String(val), copyBtn));
  row.appendChild(lblWrap);
  row.appendChild(valEl);
  row.appendChild(copyBtn);
  return row;
}

// ===== API 调用 =====
async function api(path, method = "GET", body = null, auth = true) {
  const headers = { "Content-Type": "application/json" };
  if (auth && token) headers["Authorization"] = "Bearer " + token;
  const resp = await fetch(API + path, { method, headers, body: body ? JSON.stringify(body) : null });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const err = new Error(data.error || ("请求失败 HTTP " + resp.status));
    err.status = resp.status;
    throw err;
  }
  return data;
}

// ===== Rust 层调用（Tauri command，本地系统操作）=====
async function invokeRust(cmd, args = {}) {
  if (!window.__TAURI__ || !window.__TAURI__.core) {
    throw new Error("本地功能不可用（需在桌面客户端运行）");
  }
  return await window.__TAURI__.core.invoke(cmd, args);
}

// ===== 视图切换 =====
function showLogin() {
  $("login-view").style.display = "flex";
  $("main-view").style.display = "none";
}
function showMain() {
  $("login-view").style.display = "none";
  $("main-view").style.display = "flex";
}
function switchView(name) {
  document.querySelectorAll(".nav-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.view === name);
  });
  document.querySelectorAll(".view").forEach((el) => {
    el.classList.toggle("active", el.id === name + "-view");
  });
  if (name === "account") fillAccount();
}

// ===== 登录 =====
async function login() {
  const username = $("username").value.trim();
  const password = $("password").value;
  const err = $("login-error");
  err.textContent = "";
  if (!username || !password) { err.textContent = "请输入用户名和密码"; return; }
  const btn = $("login-btn");
  btn.disabled = true;
  btn.textContent = "登录中…";
  try {
    const resp = await fetch(API + "/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) { err.textContent = data.error || "登录失败"; return; }
    token = data.token;
    user = data.user;
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
    $("user-name").textContent = user ? user.username : "";
    showMain();
    switchView("nodes");
    loadNodes();
    startPolling();
  } catch (e) {
    err.textContent = "网络错误：" + e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "登录";
  }
}

// ===== 退出 =====
async function logout(reason = "") {
  stopPolling();
  try {
    await stopLocalProxy();
    await setSystemProxy(false);
  } catch (_) { /* 忽略本地停代理失败 */ }
  upstreamNode = null;
  proxyState.mode = "off";
  token = "";
  user = null;
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  if (reason) {
    const err = $("login-error");
    if (err) err.textContent = reason;
  }
  showLogin();
}

// ===== 加载节点 =====
let lastNodeIds = "";  // 上次节点列表的 id 集合（静默轮询对比）

async function checkUpstreamRemoved(nodes) {
  if (!upstreamNode) return;
  const stillExists = (nodes || []).some((n) => n.resource_id === upstreamNode.resource_id);
  if (stillExists) return;
  const removedName = upstreamNode.name || upstreamNode.ip || upstreamNode.resource_id;
  upstreamNode = null;
  proxyState.mode = "off";
  await stopLocalProxy();
  await setSystemProxy(false);
  renderProxyUI();
  showMsg(`⚠️ 节点「${removedName}」已被管理员取消，代理已停止`, "warn");
}

async function checkUpstreamFlowExhausted(nodes) {
  if (!upstreamNode) return;
  const up = (nodes || []).find((n) => n.resource_id === upstreamNode.resource_id);
  if (!up || !up.flow_exhausted) return;
  const name = upstreamNode.name || upstreamNode.ip || upstreamNode.resource_id;
  upstreamNode = null;
  proxyState.mode = "off";
  await stopLocalProxy();
  await setSystemProxy(false);
  renderProxyUI();
  showMsg(`⚠️ 节点「${name}」流量已用尽，代理已停止，请联系管理员`, "warn");
}

let pollTimer = null;
async function pollNodes() {
  if (!token) return;
  try {
    const data = await api("/me/nodes");
    const nodes = data.nodes || [];
    await checkUpstreamRemoved(nodes);
    await checkUpstreamFlowExhausted(nodes);
    // 签名包含所有会变化的字段（到期/切换次数/流量），任一变化才重渲染
    const sig = nodes.map((n) => [
      n.resource_id,
      n.client_expiry || "",
      n.expiry || "",
      n.switch_remaining,
      n.switch_limit,
      n.flow_used_gb,
      n.flow_quota_gb,
      n.flow_exhausted ? 1 : 0,
    ].join("|")).sort().join(",");
    if (sig !== lastNodeIds) {
      lastNodeIds = sig;
      renderNodes(nodes);
    }
  } catch (e) {
    if (e.status === 401 || e.status === 403) {
      logout("账号已被禁用或登录已失效，请重新登录");
    }
    /* 其他错误（网络抖动等）静默，下一轮再试 */
  }
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(pollNodes, 30000);
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

async function loadNodes() {
  const content = $("nodes-content");
  content.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const data = await api("/me/nodes");
    const nodes = data.nodes || [];
    $("user-name").textContent = user ? user.username : "";
    // 到期：以最久的节点到期时间为准（优先客户到期 client_expiry）
    let maxExpiry = "";
    for (const n of nodes) {
      const e = (n.client_expiry || n.expiry || "").slice(0, 10);
      if (e && e > maxExpiry) maxExpiry = e;
    }
    const exp = maxExpiry || (user && user.expires_at ? formatExpiry(user.expires_at) : "不过期");
    $("user-expiry").textContent = "到期：" + exp;
    await checkUpstreamRemoved(nodes);
    await checkUpstreamFlowExhausted(nodes);
    lastNodeIds = nodes.map((n) => [
      n.resource_id,
      n.client_expiry || "",
      n.expiry || "",
      n.switch_remaining,
      n.switch_limit,
      n.flow_used_gb,
      n.flow_quota_gb,
      n.flow_exhausted ? 1 : 0,
    ].join("|")).sort().join(",");
    renderNodes(nodes);
    initColumnResize();
  } catch (e) {
    const msg = e && e.message ? String(e.message) : "";
    if ((e && (e.status === 401 || e.status === 403)) || msg.includes("401") || msg.includes("token")) {
      await logout("账号已被禁用或登录已失效，请重新登录");
      return;
    }
    content.innerHTML = '<div class="empty">加载失败：' + escapeHtml(msg) + "</div>";
  }
}

function formatExpiry(iso) {
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  } catch (_) { return iso; }
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ===== 代理控制（UI 状态，功能后续接 Rust） =====
let proxyState = { mode: "off", proto: "reality" };
const LOCAL_PROXY_PORT = 7890;  // 本地代理进程监听端口（sing-box 监听这里）
let upstreamNode = null;  // 当前上游节点对象（null = 未设置上游）

// 系统代理开关（独立）
async function setSystemProxy(enable) {
  try {
    await invokeRust("set_system_proxy", { enable, host: "127.0.0.1", port: LOCAL_PROXY_PORT });
  } catch (_) {}
}

function protoName(p) { return { socks5: "SOCKS5", ss: "SS", reality: "Reality", hysteria2: "Hysteria2" }[p] || p; }

// 唯一渲染函数：根据状态统一更新 模式按钮 + 协议按钮 + 状态栏 + 上游按钮
function renderProxyUI() {
  document.querySelectorAll("#sys-mode .seg-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.mode === proxyState.mode);
  });
  document.querySelectorAll("#proto-seg .seg-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.proto === proxyState.proto);
  });
  const st = $("proxy-status");
  if (!upstreamNode) {
    st.textContent = "未设置上游";
    st.classList.remove("connected");
  } else {
    const modeName = proxyState.mode === "local" ? "本地代理" : "全局代理";
    st.textContent = `${modeName} · ${protoName(proxyState.proto)} · 127.0.0.1:${LOCAL_PROXY_PORT}`;
    st.classList.add("connected");
  }
  updateUpstreamButtons();
}

// 切模式（没上游时不允许开代理模式）
async function setMode(mode) {
  if (!upstreamNode && mode !== "off") return;
  proxyState.mode = mode;
  renderProxyUI();
  await setSystemProxy(mode !== "off");
}

// 切协议（上游在跑时用新协议重启本地代理）
async function setProto(proto) {
  proxyState.proto = proto;
  renderProxyUI();
  if (upstreamNode) {
    try { await startLocalProxy(upstreamNode); } catch (_) {}
  }
}

// ===== 节点渲染 =====
const PROTO_DEFS = [
  { key: "socks5", title: "SOCKS5", fields: [["端口", "port"], ["用户名", "username"], ["密码", "password"]] },
  { key: "ss", title: "Shadowsocks（SS）", fields: [["端口", "port"], ["加密", "method"], ["密码", "password"]] },
  { key: "reality", title: "VLESS Reality", fields: [["端口", "port"], ["UUID", "uuid"], ["流控", "flow"], ["SNI", "sni"], ["指纹", "fp"], ["公钥", "public_key"], ["Short ID", "short_id"]] },
  { key: "hysteria2", title: "Hysteria2", fields: [["端口", "port"], ["密码", "password"], ["端口跳跃", "mport"]] },
];

let nodeCache = [];  // 当前节点列表缓存（切换后测试各协议用）

// 节点备注（持久化到服务器 D1，按 resource_id 存，换设备/重装都不丢）
function getNodeNote(n) {
  return n.note || "";
}
async function saveNodeNote(resourceId, text) {
  try {
    await api("/me/note", "POST", { resource_id: resourceId, note: text });
  } catch (e) {
    logMsg("备注保存失败：" + (e.message || "网络错误"), "err");
  }
}

function renderNodes(nodes) {
  nodeCache = nodes;
  const content = $("nodes-content");
  if (!nodes.length) {
    content.innerHTML = '<tr><td colspan="12" class="empty">暂无分配的节点，请联系管理员。</td></tr>';
    return;
  }
  content.innerHTML = "";
  nodes.forEach((n, i) => content.appendChild(buildNodeRow(n, i + 1)));
}

// ===== 节点拖拽排序（持久化到服务器）=====
let dragFromId = null;

function enableDragSort(tr, rid) {
  tr.draggable = true;
  tr.dataset.rid = rid;
  tr.addEventListener("dragstart", (e) => {
    dragFromId = rid;
    tr.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    try { e.dataTransfer.setData("text/plain", rid); } catch (_) {}
  });
  tr.addEventListener("dragend", () => {
    tr.classList.remove("dragging");
    dragFromId = null;
    document.querySelectorAll("#nodes-content tr.drag-over").forEach((r) => r.classList.remove("drag-over"));
  });
  tr.addEventListener("dragover", (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (!tr.classList.contains("drag-over")) tr.classList.add("drag-over");
  });
  tr.addEventListener("dragleave", () => tr.classList.remove("drag-over"));
  tr.addEventListener("drop", async (e) => {
    e.preventDefault();
    tr.classList.remove("drag-over");
    if (!dragFromId || dragFromId === rid) return;
    const fromIdx = nodeCache.findIndex((n) => n.resource_id === dragFromId);
    const toIdx = nodeCache.findIndex((n) => n.resource_id === rid);
    if (fromIdx < 0 || toIdx < 0) return;
    const [moved] = nodeCache.splice(fromIdx, 1);
    nodeCache.splice(toIdx, 0, moved);
    try {
      renderNodes(nodeCache);
      await api("/me/reorder", "POST", { ordered_ids: nodeCache.map((n) => n.resource_id) });
      logMsg("节点顺序已保存", "ok");
    } catch (e) {
      logMsg("排序保存失败：" + (e && e.message ? e.message : "网络错误"), "err");
    }
  });
}

function buildNodeRow(n, idx) {
  const isVultr = n.resource_type === "vultr";
  const tr = document.createElement("tr");

  const tdIdx = document.createElement("td");
  tdIdx.className = "col-idx";
  tdIdx.textContent = idx;
  tr.appendChild(tdIdx);

  const tdType = document.createElement("td");
  const badge = document.createElement("span");
  badge.className = "type-badge type-vultr";
  badge.textContent = "Vultr";
  tdType.appendChild(badge);
  tr.appendChild(tdType);

  const tdName = document.createElement("td");
  tdName.textContent = n.name || "Vultr 线路";
  tr.appendChild(tdName);

  const tdLoc = document.createElement("td");
  tdLoc.textContent = n.location || "—";
  tr.appendChild(tdLoc);

  const tdIp = document.createElement("td");
  tdIp.className = "mono";
  tdIp.textContent = n.ip || "—";
  tr.appendChild(tdIp);

  const tdDomain = document.createElement("td");
  tdDomain.className = "mono";
  tdDomain.textContent = n.domain || n.host || "—";
  tr.appendChild(tdDomain);

  const tdStatus = document.createElement("td");
  const st = document.createElement("span");
  st.id = "node-status-" + n.resource_id;
  st.className = "status pending";
  st.textContent = "未检测";
  tdStatus.appendChild(st);
  tr.appendChild(tdStatus);

  // 分配时间
  const tdCreated = document.createElement("td");
  tdCreated.textContent = n.created_at || "—";
  tr.appendChild(tdCreated);

  // 到期时间（优先客户到期 client_expiry，fallback 服务器到期 expiry；提前 3 天橙色提醒，已过期红色）
  const tdExpiry = document.createElement("td");
  const expiry = String(n.client_expiry || n.expiry || "");
  const todayStr = new Date().toISOString().slice(0, 10);
  if (expiry) {
    const expKey = expiry.slice(0, 10);
    const expDate = new Date(expKey + "T00:00:00");
    const todayDate = new Date(todayStr + "T00:00:00");
    const daysLeft = Math.ceil((expDate - todayDate) / 86400000);
    if (daysLeft <= 0) {
      tdExpiry.textContent = daysLeft === 0 ? "今天到期" : "已过期";
      tdExpiry.className = "cell-danger";
    } else if (daysLeft <= 3) {
      tdExpiry.textContent = expKey + "（" + daysLeft + "天后到期）";
      tdExpiry.className = "cell-warn";
    } else {
      tdExpiry.textContent = expKey;
    }
  } else {
    tdExpiry.textContent = "未设置";
    tdExpiry.className = "cell-muted";
  }
  tr.appendChild(tdExpiry);

  // 切换次数（已用/每天上限）
  const tdSwitch = document.createElement("td");
  const remaining = n.switch_remaining;
  const limit = n.switch_limit || 5;
  const used = Math.max(0, limit - remaining);
  tdSwitch.textContent = used + "/" + limit;
  if (remaining <= 0) tdSwitch.className = "cell-danger";
  tr.appendChild(tdSwitch);

  // 流量（已用/配额，用完红色）
  const tdFlow = document.createElement("td");
  const flowQuota = Number(n.flow_quota_gb) || 0;
  const flowUsed = Number(n.flow_used_gb) || 0;
  if (flowQuota > 0) {
    tdFlow.textContent = flowUsed.toFixed(1) + "G / " + Math.round(flowQuota) + "G";
    if (n.flow_exhausted) tdFlow.className = "cell-danger";
  } else {
    tdFlow.textContent = "不限";
    tdFlow.className = "cell-muted";
  }
  tr.appendChild(tdFlow);

  // 备注（手动输入，本地持久化）
  const tdNote = document.createElement("td");
  const noteInput = document.createElement("input");
  noteInput.type = "text";
  noteInput.className = "note-input";
  noteInput.placeholder = "备注";
  noteInput.value = getNodeNote(n);
  noteInput.addEventListener("change", () => saveNodeNote(n.resource_id, noteInput.value.trim()));
  tdNote.appendChild(noteInput);
  tr.appendChild(tdNote);

  const tdOps = document.createElement("td");
  tdOps.className = "col-ops";
  const ops = document.createElement("div");
  ops.className = "row-ops";

  const upBtn = document.createElement("button");
  upBtn.className = "btn upstream-btn";
  upBtn.dataset.id = n.resource_id;
  upBtn.textContent = (upstreamNode && upstreamNode.resource_id === n.resource_id) ? "取消上游" : "设为上游";
  upBtn.addEventListener("click", () => setUpstream(n, upBtn));
  ops.appendChild(upBtn);

  {
    const swBtn = document.createElement("button");
    swBtn.className = "btn";
    swBtn.id = "switch-" + (n.resource_id || "");
    const expKey = expiry ? expiry.slice(0, 10) : "";
    const expDate = expKey ? new Date(expKey + "T00:00:00") : null;
    const todayDate = new Date(todayStr + "T00:00:00");
    const expired = expDate && (expDate - todayDate) / 86400000 <= 0;
    if (expired) {
      swBtn.textContent = "已到期";
      swBtn.disabled = true;
    } else if (n.flow_exhausted) {
      swBtn.textContent = "流量用尽";
      swBtn.disabled = true;
    } else if (isVultr && remaining <= 0) {
      swBtn.textContent = "次数用完";
      swBtn.disabled = true;
    } else {
      swBtn.textContent = "切换IP";
      swBtn.addEventListener("click", () => switchIp(n, swBtn));
    }
    ops.appendChild(swBtn);
  }

  const checkBtn = document.createElement("button");
  checkBtn.className = "btn";
  checkBtn.textContent = "查联通性";
  checkBtn.addEventListener("click", () => checkNode(n, checkBtn));
  ops.appendChild(checkBtn);

  const links = n.links || {};
  if (Object.keys(links).length > 0) {
    const copyBtn = document.createElement("button");
    copyBtn.className = "btn";
    copyBtn.textContent = "复制节点";
    copyBtn.addEventListener("click", () => openShareDialog(n));
    ops.appendChild(copyBtn);
  }

  tdOps.appendChild(ops);
  tr.appendChild(tdOps);
  enableDragSort(tr, n.resource_id);
  return tr;
}

// ===== 设为上游（toggle）+ 默认协议选中 =====
function availableProtos(n) {
  const order = ["ss", "reality", "hysteria2"];
  return order.filter((p) => n[p] || (n.links && n.links[p]));
}

function buildSingBoxConfig(n, proto, listenPort = LOCAL_PROXY_PORT) {
  // 客户软路由用「域名」连接，客户端连接与测试也用域名（核心卖点：切 IP 不改域名）。
  // 优先域名，否则退回 host/IP（老节点可能没存域名）。
  const host = (n.domain && String(n.domain).includes(".")) ? n.domain : (n.host || n.ip || "");
  const config = {
    log: { level: "error", timestamp: false },
    inbounds: [{ type: "mixed", tag: "mixed-in", listen: "127.0.0.1", listen_port: listenPort }],
    outbounds: [{ type: "direct", tag: "direct" }],
    // 用腾讯 DNS 解析域名（更新快，绕过系统 DNS 缓存旧 IP）。
    // sing-box 1.12+ 新格式：type + server（旧 address 字段已弃用，1.14 直接报错）。
    // 不设 detour：DNS 查询默认走 direct 直连（设 detour 到空 direct 出站反而报错）。
    dns: { servers: [{ type: "udp", tag: "dns-real", server: "119.29.29.29" }] },
  };
  let upstream = null;
  if (proto === "socks5" && n.socks5) {
    upstream = { type: "socks", tag: "upstream", server: host, server_port: n.socks5.port, username: n.socks5.username || "", password: n.socks5.password || "" };
  } else if (proto === "ss" && n.ss) {
    upstream = { type: "shadowsocks", tag: "upstream", server: host, server_port: n.ss.port, method: n.ss.method || "aes-256-gcm", password: n.ss.password || "" };
  } else if (proto === "reality" && n.reality) {
    upstream = {
      type: "vless", tag: "upstream", server: host, server_port: n.reality.port,
      uuid: n.reality.uuid || "", flow: n.reality.flow || "xtls-rprx-vision",
      tls: {
        enabled: true,
        server_name: n.reality.sni || "www.cloudflare.com",
        utls: { enabled: true, fingerprint: n.reality.fp || "chrome" },
        reality: { enabled: true, public_key: n.reality.public_key || "", short_id: n.reality.short_id || "" },
      },
    };
  } else if (proto === "hysteria2" && n.hysteria2) {
    // Hysteria2 用 password 认证，TLS 证书只做加密通道不做身份验证，故 insecure=true 跳过证书校验
    upstream = {
      type: "hysteria2", tag: "upstream", server: host, server_port: n.hysteria2.port,
      password: n.hysteria2.password || "",
      tls: { enabled: true, insecure: true },
    };
  }
  if (!upstream) throw new Error("该节点缺少所选协议的配置");
  config.outbounds.push(upstream);
  config.route = { final: "upstream" };
  return JSON.stringify(config);
}

async function startLocalProxy(n) {
  const proto = proxyState.proto || "socks5";
  const cfg = buildSingBoxConfig(n, proto);
  await invokeRust("start_proxy", { configJson: cfg });
}

async function stopLocalProxy() {
  try { await invokeRust("stop_proxy"); } catch (_) {}
}

async function setUpstream(n, btn) {
  if (upstreamNode && upstreamNode.resource_id === n.resource_id) {
    // 取消上游
    upstreamNode = null;
    proxyState.mode = "off";
    await stopLocalProxy();
    await setSystemProxy(false);
    renderProxyUI();
  } else {
    // 设为上游
    const protos = availableProtos(n);
    if (!protos.length) {
      btn.textContent = "无可用协议";
      setTimeout(() => renderProxyUI(), 2000);
      return;
    }
    upstreamNode = n;
    // 保持用户已选协议；未选或当前节点不支持时优先 reality（Reality 走 xray 常驻，比 SS/gost 更稳）
    if (!protos.includes(proxyState.proto)) {
      proxyState.proto = protos.includes("reality") ? "reality" : protos[0];
    }
    proxyState.mode = "local";
    renderProxyUI();
    try {
      await startLocalProxy(n);
      await setSystemProxy(true);
    } catch (e) {
      upstreamNode = null;
      proxyState.mode = "off";
      await setSystemProxy(false);
      btn.textContent = "启动失败: " + (e.message || e);
      setTimeout(() => renderProxyUI(), 2000);
    }
  }
}

function updateUpstreamButtons() {
  document.querySelectorAll(".upstream-btn").forEach((b) => {
    const isUp = upstreamNode && b.dataset.id === upstreamNode.resource_id;
    b.textContent = isUp ? "取消上游" : "设为上游";
    b.classList.toggle("active-upstream", isUp);
  });
}
// 真实转发测试的临时本地端口（不碰 7890 连接端口，避免测试打断正在使用的连接）
const TEST_PROXY_PORT = 7891;

// 测试节点各协议「真实转发」（不是只测端口通）：以客户本机为标准，
// 用 sing-box 走「域名 → 协议握手 → 转发到 gstatic 204」，真正收到 204 才算能代理。
// 端口通 ≠ 能代理（gost 卡死端口通但转发断、脏 IP 端口通但客户本机连不上），必须真实转发才可靠。
async function testNodeProtocols(n, showDiagnostics = false) {
  const protos = ["socks5", "ss", "reality", "hysteria2"];
  const results = {};
  for (const p of protos) {
    const cfg = n[p];
    if (!cfg || !cfg.port) { results[p] = null; continue; }
    try {
      const configJson = buildSingBoxConfig(n, p, TEST_PROXY_PORT);
      const ms = await invokeRust("test_proxy", { configJson, testPort: TEST_PROXY_PORT });
      results[p] = Math.round(ms);
    } catch (e) {
      if (showDiagnostics) logMsg(`   [诊断] ${p} 真实转发测试失败: ${e}`, "warn");
      results[p] = -1;  // 不可达
    }
  }
  return results;
}

// 根据各协议测试结果更新状态列（至少一个 TCP 协议可达才"可用"；Hysteria2 UDP 探测不可靠，不参与判断）
function updateNodeStatus(resourceId, results) {
  const el = $("node-status-" + resourceId);
  if (!el) return;
  const tcpReachable = ["socks5", "ss", "reality"].some((p) => results[p] > 0);
  if (tcpReachable) {
    el.textContent = "可用";
    el.className = "status ok";
  } else {
    el.textContent = "不可用";
    el.className = "status bad";
  }
}

async function checkNode(n, btn) {
  const orig = btn.textContent;
  btn.textContent = "检测中…";
  btn.disabled = true;
  const name = n.name || n.ip || n.resource_id;
  try {
    const results = await testNodeProtocols(n, true);
    updateNodeStatus(n.resource_id, results);
    const reachable = ["socks5", "ss", "reality"].filter((p) => results[p] > 0).length;
    btn.textContent = reachable > 0 ? "✓ 可用" : "✗ 不可达";
    // 写日志回显：逐行，可达打 √、不可达打 ✗
    logMsg(`检测节点 ${name}：${reachable > 0 ? "可达" : "全部不可达"}`, reachable > 0 ? "ok" : "err");
    for (const [p, v] of Object.entries(results)) {
      if (v === null) continue;
      const label = v > 0 ? "（真实转发）" : "";
      logMsg(`   ${protoName(p)}：${v > 0 ? v + "ms" + label : "不可达"}`, v > 0 ? "ok" : "err");
    }
  } catch (e) {
    btn.textContent = "✗ 不可达";
    logMsg(`检测失败：${e.message || e}`, "err");
  }
  setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 2000);
}

// ===== 切换 IP =====
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

// 日志区：模仿服务端回显（时间戳 [HH:MM:SS] + √/✗ + 内容，滚动）
function logMsg(msg, type) {
  const body = $("log-body");
  if (!body) return;
  const line = document.createElement("div");
  line.className = "log-line" + (type ? " log-" + type : "");
  const ts = new Date().toTimeString().slice(0, 8);
  const prefix = type === "ok" ? "√ " : type === "err" ? "✗ " : "";
  const tsSpan = document.createElement("span");
  tsSpan.className = "log-ts";
  tsSpan.textContent = `[${ts}] `;
  line.appendChild(tsSpan);
  line.appendChild(document.createTextNode(prefix + msg));
  body.appendChild(line);
  body.scrollTop = body.scrollHeight;
}

function showMsg(text, type) {
  // 只写日志区（历史记录，带时间戳），顶部即时提示已去除
  const logType = type === "success" ? "ok" : type === "error" ? "err" : type === "warn" ? "warn" : "info";
  String(text).split("\n").forEach((l) => logMsg(l, logType));
}

// 自动重切计数（模块级，防止无限重切；新机卡死时最多自动重切 2 次）
let autoReSwitchCount = 0;

// 切换完成后：重新加载节点 + 等服务起来 + 测试各协议延迟 + 完整回显
async function afterSwitch(newIp) {
  await loadNodes();
  const newNode = nodeCache.find((n) => (n.ip && n.ip === newIp) || (n.host && n.host === newIp));
  const nodeName = newNode ? (newNode.name || newNode.ip || newIp) : newIp;
  if (!newNode) {
    logMsg(`切换完成，${nodeName} 新 IP：${newIp}`, "ok");
    return;
  }
  logMsg(`切换完成，${nodeName} 新 IP：${newIp}`, "ok");
  logMsg(`新机服务启动中（约1-3分钟，首次需下载安装服务），自动检测各协议…`, "info");
  // 轮询测试，直到 TCP 协议（SOCKS5/SS/Reality）至少一个可达，最多 8 次（约 2 分钟）
  // 现在 Worker done 已基于「cloud-init 回写 node-ready」（安装完成），此处是验证 + 兜底
  let results = null;
  for (let i = 0; i < 8; i++) {
    await sleep(15000);
    results = await testNodeProtocols(newNode);
    const tcpReachable = ["socks5", "ss", "reality"].some((p) => results[p] > 0);
    if (tcpReachable) break;
  }
  if (!results) return;
  updateNodeStatus(newNode.resource_id, results);
  const tcpProtos = ["socks5", "ss", "reality"];
  const reachable = tcpProtos.filter((p) => results[p] > 0).length;
  const total = tcpProtos.filter((p) => results[p] !== null).length;
  if (reachable > 0) {
    autoReSwitchCount = 0;  // 切换成功，重置自动重切计数
    logMsg(`检测完成（${reachable}/${total} 协议可用）`, "ok");
    for (const [p, v] of Object.entries(results)) {
      if (v === null) continue;
      const label = v > 0 ? "（真实转发）" : "";
      logMsg(`   ${protoName(p)}：${v > 0 ? v + "ms" + label : "不可达"}`, v > 0 ? "ok" : "err");
    }
    // 域名同步检测：提示软路由客户域名是否已指向新 IP（核心卖点：客户不用改配置）
    const domain = newNode.domain || "";
    if (domain && domain.includes(".")) {
      try {
        const resolved = await invokeRust("resolve_domain", { domain });
        const matched = String(resolved).split(",").map((s) => s.trim()).includes(newIp);
        if (matched) {
          logMsg(`域名 ${domain} 已指向新 IP，软路由客户 1 分钟内自动生效，无需改配置`, "ok");
        } else {
          logMsg(`域名 ${domain} → ${resolved}（DNS 同步中，软路由客户稍等 1 分钟生效）`, "warn");
        }
      } catch (_) {}
    }
  } else {
    // 检测不通 = 新机可能卡死（网络栈卡死/cloud-init 失败），自动重切（最多 2 次）
    if (autoReSwitchCount < 2) {
      autoReSwitchCount++;
      logMsg(`新机服务启动超时，自动重切第 ${autoReSwitchCount} 次…`, "warn");
      const btn = $("switch-" + newNode.resource_id);
      if (btn) {
        await switchIp(newNode, btn, true);  // auto 模式：跳过地区弹窗
      } else {
        logMsg(`未找到切换按钮，请手动重试`, "warn");
        autoReSwitchCount = 0;
      }
    } else {
      logMsg(`已自动重切 2 次仍不通，请稍后手动重试或联系管理员`, "err");
      autoReSwitchCount = 0;
    }
  }
}

// 地区映射（切换 IP 时选同国家地区）
const REGIONS = {
  "美国": ["atl", "ord", "dfw", "ewr", "lax", "mia", "sea", "sjc"],
  "日本": ["nrt", "itm"],
  "韩国": ["icn"],
  "新加坡": ["sgp"],
  "英国": ["lhr", "man"],
  "德国": ["fra"],
  "荷兰": ["ams"],
  "法国": ["cdg"],
  "加拿大": ["yto"],
  "澳大利亚": ["syd", "mel"],
  "印度": ["bom", "del"],
};
const REGION_NAMES = {
  "atl": "亚特兰大", "ord": "芝加哥", "dfw": "达拉斯", "ewr": "纽约",
  "lax": "洛杉矶", "mia": "迈阿密", "sea": "西雅图", "sjc": "圣何塞", "hnl": "檀香山",
  "nrt": "东京", "itm": "大阪", "icn": "首尔", "sgp": "新加坡",
  "lhr": "伦敦", "man": "曼彻斯特", "fra": "法兰克福", "ams": "阿姆斯特丹",
  "cdg": "巴黎", "yto": "多伦多", "syd": "悉尼", "mel": "墨尔本",
  "bom": "孟买", "del": "德里",
};

// 选择目标地区（同国家），返回 region 代码（空=随机，null=取消切换）
function pickRegion(n) {
  return new Promise((resolve) => {
    const loc = n.location || "";
    const country = loc.split("·")[0].trim();
    const regions = REGIONS[country];
    if (!regions || regions.length <= 1) { resolve(""); return; }
    $("region-title").textContent = `选择目标地区（${country}）`;
    const list = $("region-list");
    list.innerHTML = "";
    regions.forEach((r) => {
      const b = document.createElement("button");
      b.className = "region-option";
      b.textContent = REGION_NAMES[r] || r;
      b.addEventListener("click", () => { closeRegionModal(); resolve(r); });
      list.appendChild(b);
    });
    $("region-random").onclick = () => { closeRegionModal(); resolve(""); };
    $("region-cancel").onclick = () => { closeRegionModal(); resolve(null); };
    $("region-close").onclick = () => { closeRegionModal(); resolve(null); };
    $("region-modal").style.display = "flex";
  });
}

function closeRegionModal() {
  const m = $("region-modal");
  if (m) m.style.display = "none";
}

const STAGE_TEXT = {
  creating: "正在创建新机",
  destroying: "正在销毁旧机",
  booting: "新机已创建，正在启动中",
  installing: "新机运行中，正在下载安装代理软件",
  testing: "代理软件已就绪，正在检测端口",
  retrying: "端口检测失败，正在重建新机",
  done: "切换成功",
  failed: "切换失败",
};

async function switchIp(n, btn, auto = false) {
  if (btn.disabled) return;
  btn.disabled = true;
  const orig = btn.textContent;
  let cancelled = false;
  try {
    btn.textContent = "切换中…";
    showMsg(`正在切换 ${n.name || n.ip || n.resource_id} 的 IP…`, "info");
    // auto 模式（自动重切）跳过地区弹窗，随机选同国家地区
    const region = auto ? "" : await pickRegion(n);
    if (region === null) { cancelled = true; btn.disabled = false; btn.textContent = orig; logMsg("已取消切换", "info"); return; }  // 取消切换
    const first = await api("/me/switch", "POST", { resource_id: n.resource_id, region });
    // 检查 POST 是否真的成功：Worker 返回 error 时（如 Vultr 欠费拒绝创建新机），
    // 绝不能继续轮询（会匹配到旧 pending 记录误报「新机已创建」）
    if (first && first.error) {
      btn.textContent = "切换失败";
      showMsg("❌ " + first.error, "error");
      logMsg("切换失败：" + first.error, "err");
      return;
    }
    if (first && first.status === "done") {
      btn.textContent = "切换成功 ✓";
      await afterSwitch(first.new_ip || "");
      return;
    }
    for (let i = 0; i < 72; i++) {
      await sleep(5000);
      const st = await api("/me/switch/status");
      // 多台并发切换：按 resource_id 匹配自己这一台，避免串行混淆
      const items = st.items || [];
      const mine = items.find((it) => it.resource_id === n.resource_id);
      if (mine && mine.status === "done") {
        btn.textContent = "切换成功 ✓";
        await afterSwitch(mine.new_ip || "");
        return;
      }
      if (st.status === "none" && items.length === 0) {
        btn.textContent = "切换失败，请重试";
        showMsg("❌ 切换失败，请重试", "error");
        return;
      }
      // 阶段回显：显示当前卡在哪一步（按钮 + 日志区都回显）
      const stageText = mine ? (STAGE_TEXT[mine.stage] || mine.stage || "处理中") : "排队中";
      const noteText = mine && mine.note ? mine.note : "";
      const label = noteText || stageText;
      btn.textContent = `${stageText} ${(i + 1) * 5}s`;
      // 每 15 秒往日志区写一次（避免刷屏），让用户看清卡在哪一步
      if (i % 3 === 0) logMsg(`${label}（已等待 ${(i + 1) * 5}s）`, "info");
    }
    btn.textContent = "切换超时";
    showMsg("⚠️ 切换超时，请稍后重试", "warn");
  } catch (e) {
    btn.textContent = "失败：" + (e && e.message ? e.message : "");
    showMsg("❌ " + (e && e.message ? e.message : "网络错误"), "error");
  } finally {
    if (!cancelled) {
      btn.disabled = false;
      setTimeout(() => { btn.textContent = orig; }, 3000);
    }
  }
}

// ===== 账户页 =====
function fillAccount() {
  $("account-username").textContent = user ? user.username : "—";
  $("cur-password").value = "";
  $("new-password").value = "";
  $("account-error").textContent = "";
  $("account-success").textContent = "";
}
async function saveAccount() {
  const curPassword = $("cur-password").value;
  const newPassword = $("new-password").value;
  const err = $("account-error");
  const ok = $("account-success");
  err.textContent = "";
  ok.textContent = "";
  if (!curPassword) { err.textContent = "请输入当前密码"; return; }
  if (!newPassword) { err.textContent = "请输入新密码"; return; }
  const btn = $("account-save-btn");
  btn.disabled = true;
  btn.textContent = "保存中…";
  try {
    const body = { current_password: curPassword, new_password: newPassword };
    const data = await api("/me/account", "POST", body);
    if (data.user) {
      user = data.user;
      localStorage.setItem(USER_KEY, JSON.stringify(user));
      $("user-name").textContent = user.username || "";
    }
    ok.textContent = "修改成功";
    fillAccount();
  } catch (e) {
    err.textContent = e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "保存修改";
  }
}

// ===== 事件绑定 =====
$("login-btn").addEventListener("click", login);
$("password").addEventListener("keydown", (e) => { if (e.key === "Enter") login(); });
$("refresh-btn").addEventListener("click", loadNodes);
$("logout-btn").addEventListener("click", logout);
$("log-clear-btn").addEventListener("click", () => {
  const body = $("log-body");
  if (body) body.innerHTML = "";
});
document.querySelectorAll(".nav-item").forEach((el) => {
  el.addEventListener("click", () => switchView(el.dataset.view));
});
document.querySelectorAll("#sys-mode .seg-btn").forEach((b) => {
  b.addEventListener("click", () => setMode(b.dataset.mode));
});
document.querySelectorAll("#proto-seg .seg-btn").forEach((b) => {
  b.addEventListener("click", () => setProto(b.dataset.proto));
});
$("account-save-btn").addEventListener("click", saveAccount);


// ===== 列宽拖拽 + 持久化（复刻服务端） =====
const COLW_KEY = "proxy_client_col_widths";

function loadColWidths() {
  try { return JSON.parse(localStorage.getItem(COLW_KEY) || "[]"); } catch (_) { return []; }
}
function saveColWidths() {
  const table = document.querySelector(".nodes-table");
  if (!table) return;
  const w = [];
  table.querySelectorAll("thead th").forEach((th) => w.push(th.offsetWidth));
  localStorage.setItem(COLW_KEY, JSON.stringify(w));
}

function initColumnResize() {
  const table = document.querySelector(".nodes-table");
  if (!table) return;
  const headers = table.querySelectorAll("thead th");
  const saved = loadColWidths();

  headers.forEach((th, i) => {
    // 恢复上次保存的列宽
    if (saved[i]) {
      th.style.width = saved[i] + "px";
      th.style.minWidth = saved[i] + "px";
    }
    // 拖拽手柄（右侧边框触发）
    th.addEventListener("mousedown", (e) => {
      const rect = th.getBoundingClientRect();
      // 只在靠近右边缘 6px 内触发拖拽
      if (e.clientX < rect.right - 6) return;
      e.preventDefault();
      startColResize(e, th);
    });
  });
}

function startColResize(e, th) {
  const startX = e.clientX;
  const startW = th.offsetWidth;
  document.body.style.cursor = "col-resize";
  document.body.style.userSelect = "none";

  const move = (ev) => {
    const w = Math.max(48, startW + (ev.clientX - startX));
    th.style.width = w + "px";
    th.style.minWidth = w + "px";
  };
  const up = () => {
    document.removeEventListener("mousemove", move);
    document.removeEventListener("mouseup", up);
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    saveColWidths();
  };
  document.addEventListener("mousemove", move);
  document.addEventListener("mouseup", up);
}


// 关闭弹窗用事件委托（modal 在 <script> 之后，直接绑定会找不到元素）
function closeShareModal() {
  const m = $("share-modal");
  if (m) m.style.display = "none";
}
document.addEventListener("click", (e) => {
  const t = e.target;
  if (t && (t.id === "share-close" || t.id === "share-close-btn")) {
    closeShareModal();
  } else if (t && t.id === "share-modal") {
    closeShareModal();
  }
});

// ===== 初始化 =====
initColumnResize();
if (token) {
  $("user-name").textContent = user ? user.username : "";
  showMain();
  switchView("nodes");
  loadNodes();
  initColumnResize();
  startPolling();
} else {
  showLogin();
}

-- Cloudflare D1 全量建库（空库重建用）。
-- 已有线上库不要执行本文件：它不会给旧表补列。线上只跑尚未执行的 migrations/。
-- 用 wrangler d1 execute <db> --file=schema.sql 执行。

-- 用户表
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,                -- 格式: salt$hash (PBKDF2-SHA256)
  status TEXT NOT NULL DEFAULT 'active',      -- active / disabled
  expires_at TEXT,                            -- ISO 时间，NULL = 不过期
  note TEXT NOT NULL DEFAULT '',
  switch_limit INTEGER NOT NULL DEFAULT 5,    -- 用户级每日切换上限，节点未单独设置时用它
  switch_count INTEGER NOT NULL DEFAULT 0,
  switch_date TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- 分配表（用户 ↔ 资源）
-- resource_type: 'vultr' = Vultr 服务器节点；'relay' = webshare 前置中转节点
-- node_data: JSON 快照（host/端口/协议/订阅链接等），分配时由管理端写入
-- quota_gb: 流量配额（GB），0 = 不限；由管理端分配时写入
-- used_gb:  已用流量（GB）。Vultr 由管理端同步出站；relay 由 Worker 拉 /traffic
CREATE TABLE IF NOT EXISTS assignments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  resource_type TEXT NOT NULL,
  resource_id TEXT NOT NULL,
  node_data TEXT NOT NULL,
  created_at TEXT NOT NULL,
  switch_limit INTEGER NOT NULL DEFAULT 5,
  switch_count INTEGER NOT NULL DEFAULT 0,
  switch_date TEXT NOT NULL DEFAULT '',
  quota_gb REAL NOT NULL DEFAULT 0,
  used_gb REAL NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT '',
  sort_order INTEGER NOT NULL DEFAULT 0,
  UNIQUE(user_id, resource_type, resource_id)
);

-- 会话 token 表（客户端登录）
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assignments_user ON assignments(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

-- relay 专享：1 个 IP 只能分配给 1 个用户（不影响一台 Vultr 分给多人）
CREATE UNIQUE INDEX IF NOT EXISTS idx_relay_exclusive
  ON assignments(resource_type, resource_id) WHERE resource_type = 'relay';

-- 域名证书缓存表（切换 IP 后复用证书，避免频繁签证书触发 Let's Encrypt 限流）
CREATE TABLE IF NOT EXISTS certificates (
  domain TEXT PRIMARY KEY,
  fullchain TEXT NOT NULL,
  key TEXT NOT NULL,
  issued_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

-- 切换请求。old_used_gb 在销毁前封账；hist_gb 按 assignment_id 汇总 status=done 的行。
-- actor_type: user / admin。管理端切换的 user_id 为 0，assignment_id 用真实分配行。
-- stage: creating / booting / installed / done / failed
CREATE TABLE IF NOT EXISTS switch_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  assignment_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  old_id TEXT NOT NULL,
  new_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  new_ip TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  actor_type TEXT NOT NULL DEFAULT 'user',
  domain TEXT NOT NULL DEFAULT '',
  old_used_gb REAL NOT NULL DEFAULT 0,
  new_bandwidth INTEGER NOT NULL DEFAULT 0,
  retry_count INTEGER NOT NULL DEFAULT 0,
  stage TEXT NOT NULL DEFAULT 'creating'
);

-- 旧的线路级自动回收日志（cron 已不再写入；表保留给管理端查询历史）
CREATE TABLE IF NOT EXISTS recycle_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  resource_id TEXT NOT NULL,
  resource_type TEXT NOT NULL DEFAULT 'relay',
  user_id INTEGER,
  username TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recycle_log_created ON recycle_log(created_at);

-- 手动回收时的流量审计
CREATE TABLE IF NOT EXISTS recycle_flow_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  resource_id TEXT NOT NULL,
  username TEXT NOT NULL DEFAULT '',
  name TEXT NOT NULL DEFAULT '',
  host TEXT NOT NULL DEFAULT '',
  ws_account TEXT NOT NULL DEFAULT '',
  used_gb REAL NOT NULL DEFAULT 0,
  hist_gb REAL NOT NULL DEFAULT 0,
  reason TEXT NOT NULL DEFAULT 'manual',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recycle_flow_created ON recycle_flow_log(created_at);

-- 切换已创建新实例，但删除旧实例失败（非 404）。旧实例可能仍在计费。
CREATE TABLE IF NOT EXISTS orphan_instances (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  old_id TEXT NOT NULL,
  new_id TEXT NOT NULL,
  http_status INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

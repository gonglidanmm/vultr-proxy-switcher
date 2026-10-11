-- Cloudflare D1 表结构
-- vultr-proxy-users：IP 代理客户管理
-- 用 wrangler d1 execute <db> --file=schema.sql 执行

-- 用户表
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,                -- 格式: salt$hash (PBKDF2-SHA256)
  status TEXT NOT NULL DEFAULT 'active',      -- active / disabled
  expires_at TEXT,                            -- ISO 时间，NULL = 不过期
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- 分配表（用户 ↔ 资源）
-- resource_type: 'vultr' = Vultr 服务器节点
-- node_data: JSON 快照（host/端口/协议/订阅链接等），分配时由管理端写入
-- quota_gb: 流量配额（GB），0 = 不限；由管理端分配时写入
-- used_gb:  已用流量（GB），由管理端定期同步（Vultr API 出站累计）
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

-- 域名证书缓存表（切换 IP 后复用证书，避免频繁签证书触发 Let's Encrypt 限流）
CREATE TABLE IF NOT EXISTS certificates (
  domain TEXT PRIMARY KEY,
  fullchain TEXT NOT NULL,
  key TEXT NOT NULL,
  issued_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

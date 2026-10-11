-- 切换 IP 异步请求表（Worker 30s 限制，Vultr 实例创建需 1-2 分钟，故客户端轮询）
-- 用 wrangler d1 execute vultr-proxy-users --file=migrations/002_switch_requests.sql --remote 执行
CREATE TABLE IF NOT EXISTS switch_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  assignment_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  old_id TEXT NOT NULL,
  new_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  new_ip TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

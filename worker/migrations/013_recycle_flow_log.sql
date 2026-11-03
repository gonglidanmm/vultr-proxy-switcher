-- 回收流量审计（用户手动回收线路时记录流量，便于追溯"谁用了多少"）
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

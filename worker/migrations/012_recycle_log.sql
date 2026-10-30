-- relay 自动回收日志（phase 6）：Worker cron 到期/流量用尽自动停服务后记录，管理端回收页据此同步
CREATE TABLE IF NOT EXISTS recycle_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  resource_id TEXT NOT NULL,
  resource_type TEXT NOT NULL DEFAULT 'relay',
  user_id INTEGER,
  username TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '',   -- expired（到期）/ exhausted（流量用尽）
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recycle_log_created ON recycle_log(created_at);

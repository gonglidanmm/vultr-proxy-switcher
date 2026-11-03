-- 切换时旧 Vultr 实例删除失败（非 404）的可查询记录。不自动再创建。
-- 用 wrangler d1 execute vultr-proxy-users --remote --file=migrations/014_orphan_instances.sql 执行
CREATE TABLE IF NOT EXISTS orphan_instances (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  old_id TEXT NOT NULL,
  new_id TEXT NOT NULL,
  http_status INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

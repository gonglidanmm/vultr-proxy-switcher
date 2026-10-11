-- 迁移：加域名证书缓存表（切换 IP 后复用证书，避免频繁签证书触发限流）
-- 用法：wrangler d1 execute <db> --file=migrations/002_add_certificates.sql
CREATE TABLE IF NOT EXISTS certificates (
  domain TEXT PRIMARY KEY,
  fullchain TEXT NOT NULL,
  key TEXT NOT NULL,
  issued_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

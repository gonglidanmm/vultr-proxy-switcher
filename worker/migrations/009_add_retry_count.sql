-- 切换 IP 自动重切：记录重试次数（新机卡死时自动重建，最多重试 2 次）
-- 用 wrangler d1 execute vultr-proxy-users --file=migrations/009_add_retry_count.sql --remote 执行
ALTER TABLE switch_requests ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;

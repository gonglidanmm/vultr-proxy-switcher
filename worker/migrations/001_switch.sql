-- 切换 IP 功能：assignments 加切换次数限制字段
-- 用 wrangler d1 execute vultr-proxy-users --file=migrations/001_switch.sql --remote 执行
ALTER TABLE assignments ADD COLUMN switch_limit INTEGER NOT NULL DEFAULT 5;
ALTER TABLE assignments ADD COLUMN switch_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE assignments ADD COLUMN switch_date TEXT NOT NULL DEFAULT '';

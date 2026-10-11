-- 切换次数改为用户维度：users 表加字段（每天切换次数上限，默认 5）
-- 用 wrangler d1 execute vultr-proxy-users --file=migrations/003_user_switch.sql --remote 执行
ALTER TABLE users ADD COLUMN switch_limit INTEGER NOT NULL DEFAULT 5;
ALTER TABLE users ADD COLUMN switch_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN switch_date TEXT NOT NULL DEFAULT '';

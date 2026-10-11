-- 管理端切换 IP：switch_requests 加 actor_type 区分 user/admin 切换
-- 用 wrangler d1 execute vultr-proxy-users --file=migrations/004_switch_actor.sql --remote 执行
ALTER TABLE switch_requests ADD COLUMN actor_type TEXT NOT NULL DEFAULT 'user';

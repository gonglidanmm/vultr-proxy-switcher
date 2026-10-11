-- 管理端切换：switch_requests 加 domain（未分配服务器的 DNS 更新目标）
-- 用 wrangler d1 execute vultr-proxy-users --file=migrations/005_switch_domain.sql --remote 执行
ALTER TABLE switch_requests ADD COLUMN domain TEXT NOT NULL DEFAULT '';

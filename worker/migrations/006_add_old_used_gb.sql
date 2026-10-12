-- 切换 IP 销毁旧机前抓取的旧机出站流量（GB），用于流量累加（Vultr 不保留已销毁实例的流量明细）
-- 用 wrangler d1 execute vultr-proxy-users --remote --file=migrations/006_add_old_used_gb.sql 执行
ALTER TABLE switch_requests ADD COLUMN old_used_gb REAL NOT NULL DEFAULT 0;

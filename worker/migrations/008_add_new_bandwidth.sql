-- 切换 IP 换套餐时记录新套餐带宽（GB），切换完成后自动同步客户配额
-- 用 wrangler d1 execute vultr-proxy-users --file=migrations/008_add_new_bandwidth.sql --remote 执行
ALTER TABLE switch_requests ADD COLUMN new_bandwidth INTEGER NOT NULL DEFAULT 0;

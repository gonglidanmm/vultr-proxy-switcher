-- 迁移：assignments 表加流量配额字段
-- 用法：wrangler d1 execute <db> --file=migrations/001_add_flow_quota.sql
ALTER TABLE assignments ADD COLUMN quota_gb REAL NOT NULL DEFAULT 0;
ALTER TABLE assignments ADD COLUMN used_gb REAL NOT NULL DEFAULT 0;

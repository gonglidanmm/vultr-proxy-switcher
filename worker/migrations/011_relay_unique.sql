-- relay 专享：1 个 IP 只能分配给 1 个用户（部分唯一索引，只对 relay 生效，不影响 vultr）
-- 部分唯一索引 = 只在 resource_type='relay' 的行上强制 (resource_type, resource_id) 唯一。
CREATE UNIQUE INDEX IF NOT EXISTS idx_relay_exclusive ON assignments(resource_type, resource_id) WHERE resource_type = 'relay';

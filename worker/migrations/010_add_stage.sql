-- 切换 IP 阶段回显：记录当前阶段，服务端/客户端轮询时能看到卡在哪一步
-- 用 wrangler d1 execute vultr-proxy-users --file=migrations/010_add_stage.sql --remote 执行
-- stage 值：creating(创建新机) → destroying(销毁旧机) → booting(新机启动/装软件) → testing(测端口) → done/failed/retrying
ALTER TABLE switch_requests ADD COLUMN stage TEXT NOT NULL DEFAULT 'creating';

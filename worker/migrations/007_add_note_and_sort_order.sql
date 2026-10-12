-- 007：客户端节点备注 + 拖拽排序持久化
-- note:       客户端每节点备注（每用户每节点独立）
-- sort_order: 客户端节点显示顺序（拖拽排序后保存，越小越靠前）
ALTER TABLE assignments ADD COLUMN note TEXT NOT NULL DEFAULT '';
ALTER TABLE assignments ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0;

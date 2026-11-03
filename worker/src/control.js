// 控制面里可单测的约定：重置流量、剩余次数、停服务是否成功、孤儿实例、管理端切换记账。

export function switchRemaining(limit, count) {
  const lim = Number(limit) || 0;
  const used = Number(count) || 0;
  return Math.max(0, lim - used);
}

// 比较耗时跟期望值的长度走，不跟调用方传入的长度走。
export function timingSafeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  let result = a.length === b.length ? 0 : 1;
  const n = b.length;
  for (let i = 0; i < n; i++) {
    const ca = i < a.length ? a.charCodeAt(i) : 0;
    result |= ca ^ b.charCodeAt(i);
  }
  return result === 0;
}

export function tokenOk(presented, expected) {
  if (typeof expected !== "string" || expected.length === 0) return false;
  const token = typeof presented === "string" ? presented : "";
  return timingSafeEqual(token, expected);
}

// HTTP 200 且 body.ok === false 仍是失败（旧 /stop 会这样）。缺服务时 ok 为 true，算成功。
export function stopCallSucceeded(httpStatus, body) {
  const status = Number(httpStatus);
  const httpOk = status >= 200 && status < 300;
  return httpOk && !!body && body.ok === true;
}

// 204/200 已删除，404 本来就不在。其余（含网络失败的 0）留下可查询记录，且调用方不得再创建一台。
export function vultrDeleteNeedsOrphanRecord(httpStatus) {
  const status = Number(httpStatus);
  if (status === 404) return false;
  return status < 200 || status >= 300;
}

export function planResetFlow(body) {
  const resourceId = String((body && body.resource_id) || "").trim();
  const resourceType = String((body && body.resource_type) || "").trim();
  if (!resourceId) return { error: "resource_id 必填", status: 400 };
  if (resourceType === "vultr" || resourceType === "relay") return { kind: resourceType, resourceId };
  if (resourceType === "") return { kind: "lookup", resourceId };
  return { error: "resource_type 必须是 vultr 或 relay", status: 400 };
}

async function resetVultrUsed(db, resourceId) {
  const result = await db.prepare(
    "UPDATE assignments SET used_gb = 0 WHERE resource_type = 'vultr' AND resource_id = ?",
  ).bind(resourceId).run();
  return { ok: true, updated: (result.meta && result.meta.changes) || 0, status: 200 };
}

async function resetRelayUsed(db, resourceId) {
  const row = await db.prepare(
    "SELECT id FROM assignments WHERE resource_type = 'relay' AND resource_id = ?",
  ).bind(resourceId).first();
  if (!row) return { error: "分配不存在", status: 404 };
  await db.prepare("UPDATE assignments SET used_gb = 0 WHERE id = ?").bind(row.id).run();
  await db.prepare(
    "DELETE FROM switch_requests WHERE assignment_id = ? AND status = 'done'",
  ).bind(row.id).run();
  return { ok: true, status: 200 };
}

export async function applyResetFlow(db, body) {
  const plan = planResetFlow(body);
  if (plan.error) return plan;
  if (plan.kind === "vultr") return await resetVultrUsed(db, plan.resourceId);
  if (plan.kind === "relay") return await resetRelayUsed(db, plan.resourceId);
  const found = await db.prepare(
    "SELECT resource_type FROM assignments WHERE resource_id = ? LIMIT 1",
  ).bind(plan.resourceId).first();
  if (!found) return { error: "分配不存在", status: 404 };
  if (found.resource_type === "vultr") return await resetVultrUsed(db, plan.resourceId);
  if (found.resource_type === "relay") return await resetRelayUsed(db, plan.resourceId);
  return { error: "分配不存在", status: 404 };
}

// user_id 固定 0：管理端切换不要进用户的 /me/switch/status。没人分配时仍插一行，切换状态才有着落。
export function adminSwitchLedgerRows(assignments) {
  const rows = Array.isArray(assignments) ? assignments : [];
  if (rows.length === 0) return [{ assignment_id: 0, user_id: 0 }];
  return rows.map((a) => ({ assignment_id: Number(a.id) || 0, user_id: 0 }));
}

// assignment_id 为 0（未分配，或旧数据）时改这一台实例上的每一行，不能只取第一行。
export function assignmentsToRewrite(switchRequest, rows) {
  const list = (Array.isArray(rows) ? rows : []).filter(
    (a) => !a.resource_type || a.resource_type === "vultr",
  );
  const id = Number(switchRequest && switchRequest.assignment_id) || 0;
  if (id > 0) return list.filter((a) => Number(a.id) === id);
  return list.slice();
}

export async function recordOrphanInstance(db, { oldId, newId, httpStatus, createdAt }) {
  await db.prepare(
    "INSERT INTO orphan_instances (old_id, new_id, http_status, created_at) VALUES (?, ?, ?, ?)",
  ).bind(String(oldId), String(newId), Number(httpStatus) || 0, createdAt).run();
}

export async function listOrphanInstances(db) {
  const rows = await db.prepare(
    "SELECT old_id, new_id, http_status, created_at FROM orphan_instances ORDER BY id DESC LIMIT 200",
  ).bind().all();
  return rows.results || [];
}

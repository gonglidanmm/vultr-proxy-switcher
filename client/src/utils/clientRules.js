export const ACCOUNT_DISABLED_TEXT = "账号已禁用";

// Worker：密码正确且 users.status !== active 时，POST /login 与各 /me/* 都返回
// HTTP 403 { error: "账号已禁用" }。401 是密码错误或未登录，另一种 403 是节点到期。
export function accountDisabledText(status, data) {
  if (Number(status) !== 403) return "";
  const err = String((data && data.error) || "");
  if (err.includes("禁用")) return ACCOUNT_DISABLED_TEXT;
  return "";
}

// switch_remaining 按新公式是 limit - count。只在 <= 0 时禁用，不再为旧的 limit-1-count 补偿。
// relay 不限次数。
export function vultrSwitchLockReason(node) {
  if (!node || node.resource_type !== "vultr") return "";
  if (node.switch_remaining <= 0) return "次数用完";
  return "";
}

export const ACCOUNT_DISABLED_TEXT = "账号已禁用";

// Worker 今天对禁用账号返回 HTTP 403 + error「账号已禁用」。
// 也认 error/code 为 account_disabled，避免以后改文案后客户端又显示成未登录。
export function accountDisabledText(status, data) {
  if (Number(status) !== 403) return "";
  const body = data || {};
  const err = String(body.error || "");
  const code = String(body.code || body.error_code || "");
  if (code === "account_disabled" || err === "account_disabled" || err.includes("禁用")) {
    return ACCOUNT_DISABLED_TEXT;
  }
  return "";
}

// switch_remaining 按新公式是 limit - count。只在 <= 0 时禁用，不再为旧的 limit-1-count 补偿。
// relay 不限次数。
export function vultrSwitchLockReason(node) {
  if (!node || node.resource_type !== "vultr") return "";
  if (node.switch_remaining <= 0) return "次数用完";
  return "";
}

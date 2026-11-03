import assert from "node:assert/strict";
import test from "node:test";
import { accountDisabledText, vultrSwitchLockReason } from "./clientRules.js";

test("禁用账号显示「账号已禁用」，不和密码错误或未登录混在一起", () => {
  assert.equal(accountDisabledText(403, { error: "账号已禁用" }), "账号已禁用");
  assert.equal(accountDisabledText(403, { error: "account_disabled" }), "账号已禁用");
  assert.equal(accountDisabledText(403, { code: "account_disabled" }), "账号已禁用");
  assert.equal(accountDisabledText(401, { error: "用户名或密码错误" }), "");
  assert.equal(accountDisabledText(401, { error: "未登录或登录已过期" }), "");
  assert.equal(accountDisabledText(403, { error: "该节点已到期，无法切换 IP" }), "");
});

test("Vultr 只在 switch_remaining <= 0 时锁定；relay 不看次数", () => {
  assert.equal(vultrSwitchLockReason({ resource_type: "vultr", switch_limit: 5, switch_remaining: 1 }), "");
  assert.equal(vultrSwitchLockReason({ resource_type: "vultr", switch_limit: 5, switch_remaining: 0 }), "次数用完");
  assert.equal(vultrSwitchLockReason({ resource_type: "relay", switch_limit: 5, switch_remaining: 0 }), "");
});

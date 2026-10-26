<script setup>
import { ref, computed } from "vue";
import { useAuthStore } from "../stores/auth.js";
import { useNodesStore } from "../stores/nodes.js";
import { api } from "../api/index.js";

const auth = useAuthStore();
const nodes = useNodesStore();
const emit = defineEmits(["back"]);

const curPassword = ref("");
const newPassword = ref("");
const confirmPassword = ref("");
const saving = ref(false);
const msg = ref("");
const errMsg = ref("");

// 到期时间格式化为年月日（用户偏好：只显示年月日）
// 用户级 expires_at 为空时，回退到节点最久到期时间（每台服务器独立到期）
const expiryText = computed(() => {
  const v = auth.user?.expires_at || nodes.expiry;
  if (!v) return "未设置";
  const d = new Date(v);
  if (isNaN(d.getTime())) return v;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
});

async function changePassword() {
  errMsg.value = "";
  msg.value = "";
  if (!curPassword.value) { errMsg.value = "请输入当前密码"; return; }
  if (!newPassword.value) { errMsg.value = "请输入新密码"; return; }
  if (newPassword.value.length < 6) { errMsg.value = "新密码至少 6 位"; return; }
  if (newPassword.value !== confirmPassword.value) { errMsg.value = "两次输入的新密码不一致"; return; }
  saving.value = true;
  try {
    await api("/me/account", "POST", { current_password: curPassword.value, new_password: newPassword.value });
    msg.value = "密码修改成功";
    curPassword.value = "";
    newPassword.value = "";
    confirmPassword.value = "";
  } catch (e) {
    errMsg.value = e.message || "修改失败";
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <div class="user-view">
    <div class="user-view-head">
      <button class="back-btn" @click="emit('back')">← 返回</button>
      <h2>用户管理</h2>
    </div>
    <div class="account-card">
      <div class="account-row"><span class="row-label">用户名</span><strong class="row-value">{{ auth.user?.username || "未设置" }}</strong></div>
      <div class="account-row"><span class="row-label">到期时间</span><strong class="row-value">{{ expiryText }}</strong></div>
    </div>
    <div class="account-card">
      <h3>修改密码</h3>
      <label class="field"><span>当前密码</span><input type="password" v-model="curPassword" placeholder="请输入当前密码" /></label>
      <label class="field"><span>新密码</span><input type="password" v-model="newPassword" placeholder="至少 6 位" /></label>
      <label class="field"><span>确认新密码</span><input type="password" v-model="confirmPassword" placeholder="再次输入新密码" /></label>
      <p v-if="msg" class="ok-msg">√ {{ msg }}</p>
      <p v-if="errMsg" class="err-msg">✗ {{ errMsg }}</p>
      <button class="btn primary" :disabled="saving" @click="changePassword">{{ saving ? "提交中…" : "确认修改" }}</button>
    </div>
  </div>
</template>

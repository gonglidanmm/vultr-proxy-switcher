<script setup>
import { ref } from "vue";
import { useAuthStore } from "../stores/auth.js";
import { useNodesStore } from "../stores/nodes.js";

const auth = useAuthStore();
const nodes = useNodesStore();

const username = ref("");
const password = ref("");
const error = ref("");

async function onSubmit() {
  error.value = "";
  if (!username.value.trim() || !password.value) {
    error.value = "请输入用户名和密码";
    return;
  }
  try {
    await auth.login(username.value.trim(), password.value);
    await nodes.load();
  } catch (e) {
    error.value = auth.error || "网络错误：" + e.message;
  }
}
</script>

<template>
  <div class="login-view">
    <form class="login-card" @submit.prevent="onSubmit">
      <h1>代理客户端</h1>
      <input v-model="username" type="text" placeholder="用户名" autocomplete="username" />
      <input v-model="password" type="password" placeholder="密码" autocomplete="current-password" />
      <div v-if="error" class="login-error">{{ error }}</div>
      <button type="submit" :disabled="auth.loading">
        {{ auth.loading ? "登录中…" : "登录" }}
      </button>
    </form>
  </div>
</template>

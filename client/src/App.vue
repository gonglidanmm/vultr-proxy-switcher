<script setup>
import { ref, computed, onMounted } from "vue";
import { useAuthStore } from "./stores/auth.js";
import { useProxyStore } from "./stores/proxy.js";
import { useNodesStore } from "./stores/nodes.js";
import { closeWs } from "./api/ws.js";
import { invokeRust, API } from "./api/index.js";
import LoginView from "./components/LoginView.vue";
import NodesView from "./components/NodesView.vue";
import UserView from "./components/UserView.vue";

const auth = useAuthStore();
const isLoggedIn = computed(() => auth.isLoggedIn);
const activeView = ref("vultr"); // 默认选中 Vultr
const version = ref(""); // 客户端版本号（侧边栏底部显示）

onMounted(async () => {
  // 优先本地版本；旧客户端没有 get_version 命令则回退到服务器最新版本
  try {
    version.value = await invokeRust("get_version");
  } catch (_) {
    try {
      const resp = await fetch(API + "/version");
      const data = await resp.json().catch(() => ({}));
      version.value = data.version || "";
    } catch (_) {
      version.value = "";
    }
  }
});

function logout() {
  closeWs();
  useProxyStore().reset();
  useNodesStore().reset();
  auth.logout();
}
</script>

<template>
  <LoginView v-if="!isLoggedIn" />
  <div v-else class="app-shell">
    <aside class="sidebar">
      <div class="sidebar-brand">代理客户端</div>
      <nav class="sidebar-nav">
        <button :class="['nav-item', { active: activeView === 'vultr' }]" @click="activeView = 'vultr'">Vultr</button>
        <button :class="['nav-item', { active: activeView === 'account' }]" @click="activeView = 'account'">用户管理</button>
      </nav>
      <div class="sidebar-footer">
        <div class="sidebar-user">{{ auth.user?.username || "" }}</div>
        <div v-if="version" class="sidebar-version">版本 v{{ version }}</div>
        <button class="nav-item" @click="logout">退出登录</button>
      </div>
    </aside>
    <main class="app-main">
      <NodesView v-if="activeView === 'vultr'" />
      <UserView v-else-if="activeView === 'account'" />
    </main>
  </div>
</template>

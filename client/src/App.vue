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
const proxy = useProxyStore();
const isLoggedIn = computed(() => auth.isLoggedIn);
const activeView = ref("vultr"); // 默认选中 Vultr
const version = ref(""); // 客户端版本号（侧边栏底部显示）

onMounted(async () => {
  // 优先本地版本；旧客户端没有 get_version 命令则回退到服务器最新版本
  try {
    version.value = await invokeRust("get_version");
    proxy.log(`侧边栏版本：get_version 返回 "${version.value}"`, "info");
  } catch (e) {
    proxy.log(`侧边栏版本：get_version 失败（${e && e.message ? e.message : e}），回退服务器`, "warn");
    try {
      const resp = await fetch(API + "/version");
      const data = await resp.json().catch(() => ({}));
      version.value = data.version || "";
      proxy.log(`侧边栏版本：服务器返回 "${version.value}"`, "info");
    } catch (e2) {
      version.value = "";
      proxy.log(`侧边栏版本：服务器也失败（${e2 && e2.message ? e2.message : e2}）`, "err");
    }
  }

  // 前端热更新：每 60 秒检查构建时间戳，变化则自动刷新（客户无需退出重登）
  let currentStamp = null;
  const checkFrontendUpdate = async () => {
    try {
      const resp = await fetch(API + "/build-stamp");
      const data = await resp.json().catch(() => ({}));
      if (!data.stamp) return;
      if (currentStamp === null) {
        currentStamp = data.stamp;
      } else if (currentStamp !== data.stamp) {
        location.reload();
      }
    } catch (_) {}
  };
  setInterval(checkFrontendUpdate, 60000);
  checkFrontendUpdate();
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
        <button :class="['nav-item', { active: activeView === 'vultr' }]" @click="activeView = 'vultr'">
          <svg class="nav-icon" width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
            <rect width="24" height="24" rx="6" fill="#00c853"/>
            <path d="M13.2 3.6 6.5 13h4.1l-1.3 7.4L16.5 11h-4.3l1-7.4z" fill="#fff"/>
          </svg>
          <span>Webshare-IP</span>
        </button>
      </nav>
      <div class="sidebar-footer">
        <button class="user-row" :class="{ active: activeView === 'account' }" @click="activeView = 'account'" title="用户管理">
          <span class="user-avatar">{{ (auth.user?.username || "?").charAt(0).toUpperCase() }}</span>
          <span class="user-info">
            <span class="sidebar-username">{{ auth.user?.username || "" }}</span>
            <span v-if="version" class="sidebar-version">v{{ version }}</span>
          </span>
        </button>
        <button class="logout-btn" @click="logout">退出登录</button>
      </div>
    </aside>
    <main class="app-main">
      <NodesView v-if="activeView === 'vultr'" />
      <UserView v-else-if="activeView === 'account'" @back="activeView = 'vultr'" />
    </main>
  </div>
</template>

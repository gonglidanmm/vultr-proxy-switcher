<script setup>
import { ref, computed } from "vue";
import { useAuthStore } from "./stores/auth.js";
import { useProxyStore } from "./stores/proxy.js";
import { closeWs } from "./api/ws.js";
import LoginView from "./components/LoginView.vue";
import NodesView from "./components/NodesView.vue";
import UserView from "./components/UserView.vue";

const auth = useAuthStore();
const isLoggedIn = computed(() => auth.isLoggedIn);
const activeView = ref("vultr"); // 默认选中 Vultr

function logout() {
  closeWs();
  useProxyStore().reset();
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
        <button class="nav-item" @click="logout">退出登录</button>
      </div>
    </aside>
    <main class="app-main">
      <NodesView v-if="activeView === 'vultr'" />
      <UserView v-else-if="activeView === 'account'" />
    </main>
  </div>
</template>

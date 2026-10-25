<script setup>
import { computed } from "vue";
import { useAuthStore } from "./stores/auth.js";
import LoginView from "./components/LoginView.vue";
import NodesView from "./components/NodesView.vue";

const auth = useAuthStore();
const isLoggedIn = computed(() => auth.isLoggedIn);
</script>

<template>
  <LoginView v-if="!isLoggedIn" />
  <NodesView v-else />
</template>

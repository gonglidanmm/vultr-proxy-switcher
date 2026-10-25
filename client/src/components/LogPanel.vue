<script setup>
import { nextTick, ref, watch } from "vue";
import { useProxyStore } from "../stores/proxy.js";

const proxy = useProxyStore();
const bodyRef = ref(null);

watch(
  () => proxy.logs.length,
  async () => {
    await nextTick();
    if (bodyRef.value) bodyRef.value.scrollTop = bodyRef.value.scrollHeight;
  }
);
</script>

<template>
  <div class="log-panel">
    <div class="log-head">
      <span class="log-title">操作日志</span>
      <button class="link-btn" @click="proxy.clearLogs()">清空</button>
    </div>
    <div class="log-body" ref="bodyRef">
      <div v-if="!proxy.logs.length" class="log-empty">暂无操作记录</div>
      <div v-for="(l, i) in proxy.logs" :key="i" class="log-line" :class="'log-' + l.type">
        <span class="log-ts">[{{ l.ts }}] </span>
        <span v-if="l.type === 'ok'" class="log-mark">√ </span>
        <span v-else-if="l.type === 'err'" class="log-mark">✗ </span>
        <span>{{ l.text }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from "vue";
import { invokeRust } from "../api/index.js";

const update = ref(null); // { current, latest, note, url }

function show(latest, current, note, url) {
  update.value = { current, latest, note, url };
}

function download() {
  const url = update.value?.url;
  if (!url) return;
  if (window.__TAURI__) {
    invokeRust("open_url", { url }).catch(() => window.open(url, "_blank"));
  } else {
    window.open(url, "_blank");
  }
}

function close() {
  update.value = null;
}

defineExpose({ show });
</script>

<template>
  <div v-if="update" class="modal-backdrop" @click.self="close">
    <div class="modal update-modal">
      <div class="modal-head">
        <span class="modal-title">发现新版本</span>
        <button class="modal-close" title="关闭" @click="close">✕</button>
      </div>
      <div class="modal-body">
        <p>当前版本 <b>{{ update.current }}</b>，最新版本 <b>{{ update.latest }}</b></p>
        <p v-if="update.note" class="update-note">{{ update.note }}</p>
      </div>
      <div class="modal-footer">
        <button class="btn" @click="close">稍后再说</button>
        <button class="btn primary" @click="download">前往下载</button>
      </div>
    </div>
  </div>
</template>

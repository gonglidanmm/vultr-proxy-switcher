<script setup>
import { ref } from "vue";
import { invokeRust } from "../api/index.js";

const update = ref(null); // { current, latest, note, urls, fallback }
const trying = ref(false);

function show(latest, current, note, urls, fallback) {
  update.value = { current, latest, note, urls: urls || [], fallback: fallback || "" };
}

function openLink(url) {
  if (window.__TAURI__) {
    invokeRust("open_url", { url }).catch(() => window.open(url, "_blank"));
  } else {
    window.open(url, "_blank");
  }
}

async function download() {
  const u = update.value;
  if (!u || trying.value) return;
  trying.value = true;
  try {
    // 依次探测镜像，第一个可用的就打开（自动 fallback，一个失效换下一个）
    for (const url of u.urls) {
      let ok = false;
      try {
        ok = await invokeRust("probe_url", { url });
      } catch (e) {
        ok = false;
      }
      if (ok) {
        openLink(url);
        close();
        return;
      }
    }
    // 全部镜像不可用：打开 fallback（release 列表页）
    if (u.fallback) openLink(u.fallback);
    close();
  } finally {
    trying.value = false;
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
        <button class="btn primary" :disabled="trying" @click="download">{{ trying ? "检测下载源…" : "前往下载" }}</button>
      </div>
    </div>
  </div>
</template>

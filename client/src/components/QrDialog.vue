<script setup>
import { onMounted, ref } from "vue";
import QRCode from "qrcode";

const props = defineProps({ name: String, link: String });
const emit = defineEmits(["close"]);

const canvasRef = ref(null);
const copied = ref(false);
const error = ref("");

onMounted(renderQr);

async function renderQr() {
  try {
    await QRCode.toCanvas(canvasRef.value, props.link, {
      width: 260,
      margin: 2,
      errorCorrectionLevel: "M",
      color: { dark: "#000000", light: "#ffffff" },
    });
  } catch (e) {
    error.value = "二维码生成失败：" + (e.message || e);
  }
}

// 复制二维码图片（PNG），不是链接——方便粘贴发给别人扫码导入
async function copyImage() {
  const canvas = canvasRef.value;
  if (!canvas) return;
  try {
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
    if (!blob) throw new Error("canvas 为空");
    if (navigator.clipboard && window.ClipboardItem) {
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
      copied.value = true;
      setTimeout(() => (copied.value = false), 1500);
    } else {
      error.value = "当前环境不支持复制图片";
    }
  } catch (e) {
    error.value = "复制失败：" + (e.message || "浏览器不支持复制图片");
  }
}
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')">
    <div class="modal qr-modal">
      <div class="modal-head">
        <span class="modal-title">协议二维码 — {{ name }}</span>
        <button class="modal-close" title="关闭" @click="emit('close')">✕</button>
      </div>
      <div class="modal-body qr-body">
        <canvas ref="canvasRef"></canvas>
        <div v-if="error" class="qr-error">{{ error }}</div>
        <div class="qr-tip">手机扫描此二维码可直接导入代理软件</div>
      </div>
      <div class="modal-footer">
        <button class="btn" @click="emit('close')">关闭</button>
        <button class="btn primary" @click="copyImage">{{ copied ? '已复制 ✓' : '复制二维码' }}</button>
      </div>
    </div>
  </div>
</template>

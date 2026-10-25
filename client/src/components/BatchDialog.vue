<script setup>
defineProps({ count: Number });
const emit = defineEmits(["close", "confirm"]);
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')">
    <div class="modal">
      <div class="modal-head">
        <span class="modal-title">批量切换确认</span>
        <button class="modal-close" title="关闭" @click="emit('close')">✕</button>
      </div>
      <div class="modal-body">
        <p>确定批量切换 <b>{{ count }}</b> 台节点的 IP 吗？</p>
        <p class="batch-sub">切换随机同国家地区，每台约需几分钟。</p>
      </div>
      <div class="modal-footer">
        <button class="btn" @click="emit('close')">取消</button>
        <button class="btn primary" @click="emit('confirm')">确认切换</button>
      </div>
    </div>
  </div>
</template>

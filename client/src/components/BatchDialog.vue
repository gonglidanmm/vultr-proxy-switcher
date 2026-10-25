<script setup>
import { ref } from "vue";
import { REGIONS, REGION_NAMES } from "../utils/index.js";

const props = defineProps({ nodes: Array });
const emit = defineEmits(["close", "confirm"]);

// 每台的区域选择：resource_id -> region（空字符串 = 随机）
const selections = ref({});

function countryOf(n) {
  const loc = n.location || "";
  return loc.split("·")[0].trim();
}
function regionsOf(n) {
  return REGIONS[countryOf(n)] || [];
}

function confirm() {
  emit("confirm", selections.value);
}
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')">
    <div class="modal batch-modal">
      <div class="modal-head">
        <span class="modal-title">批量切换 — 选择每台目标地区</span>
        <button class="modal-close" title="关闭" @click="emit('close')">✕</button>
      </div>
      <div class="modal-body">
        <p class="batch-sub">每台默认「随机」（同国家），可单独指定地区；部分指定、部分随机也可以。</p>
        <div v-for="n in nodes" :key="n.resource_id" class="batch-row">
          <div class="batch-node">
            <span class="batch-name">{{ n.name || n.ip || n.resource_id }}</span>
            <span class="batch-loc">{{ n.location || "—" }}</span>
          </div>
          <select v-model="selections[n.resource_id]" class="batch-select">
            <option value="">随机（同国家）</option>
            <option v-for="r in regionsOf(n)" :key="r" :value="r">{{ REGION_NAMES[r] || r }}</option>
          </select>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn" @click="emit('close')">取消</button>
        <button class="btn primary" @click="confirm">确认切换</button>
      </div>
    </div>
  </div>
</template>

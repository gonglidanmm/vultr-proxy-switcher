<script setup>
import { computed } from "vue";
import { REGIONS, REGION_NAMES } from "../utils/index.js";

const props = defineProps({ node: Object });
const emit = defineEmits(["close", "pick"]);

const country = computed(() => {
  const loc = props.node?.location || "";
  return loc.split("·")[0].trim();
});
const regions = computed(() => REGIONS[country.value] || []);
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')">
    <div class="modal region-modal">
      <div class="modal-head">
        <span class="modal-title">选择目标地区（{{ country }}）</span>
        <button class="modal-close" title="关闭" @click="emit('close')">✕</button>
      </div>
      <div class="modal-body">
        <div class="region-grid">
          <button v-for="r in regions" :key="r" class="region-option" @click="emit('pick', r)">
            {{ REGION_NAMES[r] || r }}
          </button>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn" @click="emit('pick', '')">随机</button>
        <button class="btn primary" @click="emit('close')">取消切换</button>
      </div>
    </div>
  </div>
</template>

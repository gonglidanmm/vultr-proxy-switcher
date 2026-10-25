<script setup>
import { onMounted } from "vue";
import { useNodesStore } from "../stores/nodes.js";
import { useAuthStore } from "../stores/auth.js";

const nodes = useNodesStore();
const auth = useAuthStore();

function formatExpiry(n) {
  const expiry = String(n.client_expiry || n.expiry || "");
  if (!expiry) return { text: "未设置", cls: "cell-muted" };
  const expKey = expiry.slice(0, 10);
  const todayStr = new Date().toISOString().slice(0, 10);
  const daysLeft = Math.ceil(
    (new Date(expKey + "T00:00:00") - new Date(todayStr + "T00:00:00")) / 86400000
  );
  if (daysLeft <= 0)
    return { text: daysLeft === 0 ? "今天到期" : "已过期", cls: "cell-danger" };
  if (daysLeft <= 3)
    return { text: `${expKey}（${daysLeft}天后到期）`, cls: "cell-warn" };
  return { text: expKey, cls: "" };
}

function switchInfo(n) {
  const remaining = n.switch_remaining;
  const limit = n.switch_limit || 5;
  const used = Math.max(0, limit - remaining);
  return { text: `${used}/${limit}`, cls: remaining <= 0 ? "cell-danger" : "" };
}

function flowInfo(n) {
  const quota = Number(n.flow_quota_gb) || 0;
  const used = Number(n.flow_used_gb) || 0;
  if (quota > 0)
    return { text: `${used.toFixed(1)}G / ${Math.round(quota)}G`, cls: n.flow_exhausted ? "cell-danger" : "" };
  return { text: "不限", cls: "cell-muted" };
}

function logout() {
  auth.logout();
}

onMounted(() => {
  if (nodes.nodes.length === 0) nodes.load().catch(() => {});
});
</script>

<template>
  <div class="main-view">
    <header class="topbar">
      <div class="topbar-left">
        <span class="user-name">{{ auth.user?.username || "" }}</span>
        <span v-if="nodes.expiry" class="user-expiry">到期：{{ nodes.expiry }}</span>
      </div>
      <button class="btn" @click="logout">退出登录</button>
    </header>

    <div class="table-wrap">
      <table class="nodes-table">
        <thead>
          <tr>
            <th class="col-sel"></th>
            <th class="col-idx">#</th>
            <th>类型</th>
            <th>名称</th>
            <th>位置</th>
            <th>IP</th>
            <th>域名</th>
            <th>状态</th>
            <th>分配时间</th>
            <th>到期时间</th>
            <th>切换次数</th>
            <th>流量</th>
            <th>备注</th>
            <th class="col-ops">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="nodes.loading && nodes.nodes.length === 0">
            <td colspan="14" class="empty">加载中…</td>
          </tr>
          <tr v-else-if="!nodes.nodes.length">
            <td colspan="14" class="empty">暂无分配的节点，请联系管理员。</td>
          </tr>
          <tr v-for="(n, i) in nodes.nodes" :key="n.resource_id" :data-rid="n.resource_id">
            <td class="col-sel"><input type="checkbox" class="row-sel" :data-rid="n.resource_id" /></td>
            <td class="col-idx">{{ i + 1 }}</td>
            <td><span class="type-badge type-vultr">Vultr</span></td>
            <td>{{ n.name || "Vultr 线路" }}</td>
            <td>{{ n.location || "—" }}</td>
            <td class="mono">{{ n.ip || "—" }}</td>
            <td class="mono">{{ n.domain || n.host || "—" }}</td>
            <td><span class="status pending">未检测</span></td>
            <td class="cell-muted">{{ (n.created_at || "").slice(0, 10) || "—" }}</td>
            <td :class="formatExpiry(n).cls">{{ formatExpiry(n).text }}</td>
            <td :class="switchInfo(n).cls">{{ switchInfo(n).text }}</td>
            <td :class="flowInfo(n).cls">{{ flowInfo(n).text }}</td>
            <td><input type="text" class="note-input" placeholder="备注" :value="n.note || ''" /></td>
            <td class="col-ops">
              <div class="row-ops">
                <button class="btn">设为上游</button>
                <button class="btn">切换IP</button>
                <button class="btn">查联通性</button>
                <button v-if="n.links && Object.keys(n.links).length" class="btn">复制节点</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

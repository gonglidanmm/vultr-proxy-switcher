<script setup>
import { computed, nextTick, onMounted, reactive, ref } from "vue";
import { useNodesStore } from "../stores/nodes.js";
import { useAuthStore } from "../stores/auth.js";
import { useProxyStore } from "../stores/proxy.js";
import { api, invokeRust, API } from "../api/index.js";
import { connectWs, onSwitchMessage } from "../api/ws.js";
import { protoName, sleep, STAGE_TEXT, compareVersion } from "../utils/index.js";
import RegionModal from "./RegionModal.vue";
import ShareDialog from "./ShareDialog.vue";
import QrDialog from "./QrDialog.vue";
import LogPanel from "./LogPanel.vue";
import BatchDialog from "./BatchDialog.vue";
import UpdateDialog from "./UpdateDialog.vue";

const nodes = useNodesStore();
const auth = useAuthStore();
const proxy = useProxyStore();

const nodeStatus = reactive({}); // resource_id -> { text, cls }
const connectivityTests = reactive({}); // resource_id -> { active, protocols: {proto:{state,ms}} }
const PROTOCOLS = ["socks5", "ss", "reality", "hysteria2"];

const shareNode = ref(null);
const regionNode = ref(null);
const qr = ref(null); // { name, link }

const switching = reactive({}); // resource_id -> { text, disabled, checking }
let autoReSwitchCount = 0;

// ===== WebSocket 实时推送 =====
const switchWaiters = {}; // resource_id -> resolve 函数（等待 WS 推送 done/failed）

function handleWsMessage(data) {
  if (!data || !data.resource_id) return;
  const w = switchWaiters[data.resource_id];
  if (!w) return;
  if (data.status === "done" || data.status === "failed") {
    delete switchWaiters[data.resource_id];
    w(data);
  }
}

// ===== 批量切换 =====
const selectedIds = ref(new Set());
const batchModal = ref(false);
const batchPending = ref([]);
const selCount = computed(() => selectedIds.value.size);
const allSelected = computed(() => nodes.nodes.length > 0 && selCount.value === nodes.nodes.length);

function toggleSelectAll() {
  if (allSelected.value) {
    selectedIds.value = new Set();
  } else {
    selectedIds.value = new Set(nodes.nodes.map((n) => n.resource_id));
  }
}
function toggleSelect(id) {
  const s = new Set(selectedIds.value);
  if (s.has(id)) s.delete(id);
  else s.add(id);
  selectedIds.value = s;
}

function openBatchModal() {
  const swappable = nodes.nodes.filter((n) => {
    if (!selectedIds.value.has(n.resource_id)) return false;
    return !isSwitchDisabled(n);
  });
  if (!swappable.length) {
    proxy.log("选中的节点都不可切换（已到期/流量用尽/次数用完）", "warn");
    return;
  }
  batchPending.value = swappable;
  batchModal.value = true;
}

function doBatchSwitch(regions = {}) {
  const list = batchPending.value;
  batchPending.value = [];
  batchModal.value = false;
  proxy.log(`开始批量切换 ${list.length} 台节点…`, "info");
  // 每台用指定区域（regions[rid] 为空=随机），并行切换
  list.forEach((n) => switchIp(n, { region: regions[n.resource_id] ?? "" }));
  selectedIds.value = new Set();
}

// ===== 版本检查 =====
const updateDialog = ref(null); // UpdateDialog 组件实例

async function checkVersion() {
  try {
    let localVer = "";
    try {
      localVer = await invokeRust("get_version");
    } catch (_) {
      return; // 非桌面环境跳过
    }
    const resp = await fetch(API + "/version");
    const data = await resp.json().catch(() => ({}));
    if (!data.version) return;
    if (compareVersion(data.version, localVer) > 0) {
      updateDialog.value?.show(data.version, localVer, data.note || "", data.download_url || "");
    }
  } catch (_) { /* 忽略 */ }
}

// ===== 全部测速 =====
const speedTesting = ref(false);

async function speedTestAll() {
  if (speedTesting.value) return;
  speedTesting.value = true;
  const list = nodes.nodes;
  proxy.log(`开始测速 ${list.length} 台节点（并发）…`, "info");
  try {
    await Promise.all(list.map(async (n) => {
      const name = n.name || n.ip || n.resource_id;
      const results = await proxy.testNodeProtocols(n);
      updateNodeStatus(n.resource_id, results);
      const reachable = PROTOCOLS.filter((p) => results[p] > 0);
      proxy.log(`  ${name}：${reachable.length > 0 ? reachable.length + " 协议可达" : "全部不可达"}`, reachable.length > 0 ? "ok" : "err");
    }));
    proxy.log("测速完成", "ok");
  } catch (e) {
    proxy.log("测速失败：" + (e.message || e), "err");
  } finally {
    speedTesting.value = false;
  }
}

function formatExpiry(n) {
  const expiry = String(n.client_expiry || n.expiry || "");
  if (!expiry) return { text: "未设置", cls: "cell-muted" };
  const expKey = expiry.slice(0, 10);
  const todayStr = new Date().toISOString().slice(0, 10);
  const daysLeft = Math.ceil((new Date(expKey + "T00:00:00") - new Date(todayStr + "T00:00:00")) / 86400000);
  if (daysLeft <= 0) return { text: daysLeft === 0 ? "今天到期" : "已过期", cls: "cell-danger" };
  if (daysLeft <= 3) return { text: `${expKey}（${daysLeft}天后到期）`, cls: "cell-warn" };
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
  if (quota > 0) return { text: `${used.toFixed(1)}G / ${Math.round(quota)}G`, cls: n.flow_exhausted ? "cell-danger" : "" };
  return { text: "不限", cls: "cell-muted" };
}

function isSwitchDisabled(n) {
  const expKey = (String(n.client_expiry || n.expiry || "")).slice(0, 10);
  const todayStr = new Date().toISOString().slice(0, 10);
  const expired = expKey && (new Date(expKey + "T00:00:00") - new Date(todayStr + "T00:00:00")) / 86400000 <= 0;
  if (expired) return "已到期";
  if (n.flow_exhausted) return "流量用尽";
  if (n.resource_type === "vultr" && n.switch_remaining <= 0) return "次数用完";
  return "";
}

function updateNodeStatus(resourceId, results) {
  const tcpReachable = ["socks5", "ss", "reality"].some((p) => results[p] > 0);
  nodeStatus[resourceId] = tcpReachable
    ? { text: "可用", cls: "status ok" }
    : { text: "不可用", cls: "status bad" };
}

async function afterSwitch(newIp) {
  await nodes.load();
  const newNode = nodes.nodes.find((n) => (n.ip && n.ip === newIp) || (n.host && n.host === newIp));
  const nodeName = newNode ? (newNode.name || newNode.ip || newIp) : newIp;
  proxy.log(`切换完成，${nodeName} 新 IP：${newIp}`, "ok");
  if (!newNode) return;
  proxy.log(`新机服务启动中（约1-3分钟，首次需下载安装服务），自动检测各协议…`, "info");
  let results = null;
  for (let i = 0; i < 8; i++) {
    await sleep(15000);
    results = await proxy.testNodeProtocols(newNode);
    const tcpReachable = ["socks5", "ss", "reality"].some((p) => results[p] > 0);
    if (tcpReachable) break;
  }
  if (!results) return;
  updateNodeStatus(newNode.resource_id, results);
  const tcpProtos = ["socks5", "ss", "reality"];
  const reachable = tcpProtos.filter((p) => results[p] > 0).length;
  const total = tcpProtos.filter((p) => results[p] !== null).length;
  if (reachable > 0) {
    autoReSwitchCount = 0;
    proxy.log(`检测完成（${reachable}/${total} 协议可用）`, "ok");
    for (const [p, v] of Object.entries(results)) {
      if (v === null) continue;
      proxy.log(`   ${protoName(p)}：${v > 0 ? v + "ms（真实转发）" : "不可达"}`, v > 0 ? "ok" : "err");
    }
  } else {
    if (autoReSwitchCount < 2) {
      autoReSwitchCount++;
      proxy.log(`新机服务启动超时，自动重切第 ${autoReSwitchCount} 次…`, "warn");
      await switchIp(newNode, { auto: true });
    } else {
      proxy.log(`已自动重切 2 次仍不通，请稍后手动重试或联系管理员`, "err");
      autoReSwitchCount = 0;
    }
  }
}

async function switchIp(n, opts = {}) {
  const auto = !!opts.auto;
  const explicitRegion = opts.region; // undefined=弹窗，""=随机，指定值=用该区域
  if (switching[n.resource_id]?.disabled) return;
  switching[n.resource_id] = { text: "切换中…", disabled: true };
  try {
    proxy.logDivider();
    proxy.log(`正在切换 ${n.name || n.ip || n.resource_id} 的 IP…`, "info");
    let region = "";
    if (explicitRegion !== undefined) {
      region = explicitRegion; // 批量切换：显式指定区域（或空=随机）
    } else if (!auto) {
      const loc = n.location || "";
      const country = loc.split("·")[0].trim();
      const knownCountries = ["美国", "日本", "韩国", "新加坡", "英国", "德国", "荷兰", "法国", "加拿大", "澳大利亚", "印度"];
      if (knownCountries.includes(country)) {
        region = await pickRegion(n);
        if (region === null) {
          switching[n.resource_id] = { text: "切换IP", disabled: false };
          proxy.log("已取消切换", "info");
          return;
        }
      }
    }
    const first = await api("/me/switch", "POST", { resource_id: n.resource_id, region });
    if (first && first.error) {
      switching[n.resource_id] = { text: "切换失败", disabled: false };
      proxy.log("❌ " + first.error, "err");
      return;
    }
    if (first && first.status === "done") {
      switching[n.resource_id] = { text: "切换成功 ✓", disabled: false };
      await afterSwitch(first.new_ip || "");
      return;
    }
    // 等待 done：WS 实时推送优先，轮询兜底（15 秒一次，共 6 分钟）
    const result = await new Promise((resolve) => {
      switchWaiters[n.resource_id] = resolve; // WS 推送 done/failed 时立即 resolve
      (async () => {
        for (let i = 0; i < 24; i++) {
          await sleep(15000);
          if (!switchWaiters[n.resource_id]) return; // 已被 WS resolve，退出兜底轮询
          try {
            const st = await api("/me/switch/status");
            const items = st.items || [];
            const mine = items.find((it) => it.resource_id === n.resource_id);
            if (mine && mine.status === "done") {
              delete switchWaiters[n.resource_id];
              resolve(mine);
              return;
            }
            if (st.status === "none" && items.length === 0) {
              delete switchWaiters[n.resource_id];
              resolve({ status: "failed", note: "切换失败，请重试" });
              return;
            }
            const stageText = mine ? (STAGE_TEXT[mine.stage] || mine.stage || "处理中") : "排队中";
            const noteText = mine && mine.note ? mine.note : "";
            const label = noteText || stageText;
            switching[n.resource_id] = { text: `${stageText} ${(i + 1) * 15}s`, disabled: true };
            if (i % 2 === 0) proxy.log(`${label}（已等待 ${(i + 1) * 15}s）`, "info");
          } catch (_) { /* 网络抖动，下一轮再试 */ }
        }
        if (switchWaiters[n.resource_id]) {
          delete switchWaiters[n.resource_id];
          resolve({ status: "timeout" });
        }
      })();
    });

    if (result.status === "done") {
      switching[n.resource_id] = { text: "切换成功 ✓", disabled: false };
      await afterSwitch(result.new_ip || "");
      return;
    }
    if (result.status === "failed") {
      switching[n.resource_id] = { text: "切换失败", disabled: false };
      proxy.log("❌ " + (result.note || "切换失败，请重试"), "err");
      return;
    }
    switching[n.resource_id] = { text: "切换超时", disabled: false };
    proxy.log("⚠️ 切换超时，请稍后重试", "warn");
  } catch (e) {
    switching[n.resource_id] = { text: "失败", disabled: false };
    proxy.log("❌ " + (e && e.message ? e.message : "网络错误"), "err");
  } finally {
    setTimeout(() => {
      if (switching[n.resource_id] && switching[n.resource_id].text !== "切换成功 ✓") {
        switching[n.resource_id] = { text: "切换IP", disabled: false };
      }
    }, 3000);
  }
}

let regionResolve = null; // 当前等待地区选择的 resolve（独立变量，不挂载到节点对象）

function pickRegion(n) {
  return new Promise((resolve) => {
    regionResolve = resolve;
    regionNode.value = n;
  });
}
function onRegionPick(r) {
  const resolve = regionResolve;
  regionResolve = null;
  regionNode.value = null;
  if (resolve) resolve(r);
}
function onRegionClose() {
  const resolve = regionResolve;
  regionResolve = null;
  regionNode.value = null;
  if (resolve) resolve(null);
}

async function checkNode(n) {
  const rid = n.resource_id;
  const configured = PROTOCOLS.filter((p) => n[p] && n[p].port);
  if (configured.length === 0) {
    proxy.log(`节点 ${n.name || n.ip || rid} 没有可检测的协议配置`, "warn");
    return;
  }
  switching[rid] = { ...switching[rid], checking: true };
  connectivityTests[rid] = {
    active: true,
    protocols: Object.fromEntries(configured.map((p) => [p, { state: "checking", ms: null }])),
  };
  const name = n.name || n.ip || rid;
  proxy.logDivider();
  proxy.log(`开始检测 ${name}：${configured.length} 个协议并行启动…`, "info");
  await nextTick(); // 先把“检测中”状态画出来，再启动原生检测，避免用户看到空等
  try {
    const results = await proxy.testNodeProtocols(n, true, (p, ms) => {
      if (ms === null) return;
      connectivityTests[rid].protocols[p] = { state: ms > 0 ? "ok" : "bad", ms };
      proxy.log(`   ${protoName(p)}：${ms > 0 ? ms + "ms（真实转发）" : "不可达"}`, ms > 0 ? "ok" : "err");
    });
    updateNodeStatus(rid, results);
    const reachable = configured.filter((p) => results[p] > 0).length;
    connectivityTests[rid].active = false;
    proxy.log(`检测完成：${reachable > 0 ? reachable + " 协议可达" : "全部不可达"}`, reachable > 0 ? "ok" : "err");
  } catch (e) {
    connectivityTests[rid].active = false;
    proxy.log(`检测失败：${e.message || e}`, "err");
  } finally {
    switching[rid] = { ...switching[rid], checking: false };
  }
}

function checkProgress(n) {
  const test = connectivityTests[n.resource_id];
  if (!test) return "";
  const values = Object.values(test.protocols);
  const done = values.filter((x) => x.state !== "checking").length;
  return `${done}/${values.length}`;
}

onMounted(() => {
  if (nodes.nodes.length === 0) nodes.load().catch(() => {});
  checkVersion();
  connectWs();
  onSwitchMessage(handleWsMessage);
});
</script>

<template>
  <div class="main-view">
    <header class="topbar">
      <div class="topbar-left">
        <span class="user-name">{{ auth.user?.username || "" }}</span>
        <span v-if="nodes.expiry" class="user-expiry">到期：{{ nodes.expiry }}</span>
      </div>
      <div class="topbar-right">
        <span v-if="selCount" class="sel-count">已选 {{ selCount }} 台</span>
        <button class="btn" :disabled="selCount === 0" @click="openBatchModal">批量切换IP</button>
        <button class="btn" :disabled="speedTesting" @click="speedTestAll">{{ speedTesting ? '测速中…' : '全部测速' }}</button>
        <button class="btn" @click="nodes.load().catch(() => {})">刷新</button>
      </div>
    </header>

    <div class="proxy-card">
      <div class="proxy-row">
        <span class="proxy-label">代理模式</span>
        <div class="seg">
          <button class="seg-btn" :class="{ active: proxy.mode === 'off' }" @click="proxy.setMode('off')">不代理</button>
          <button class="seg-btn" :class="{ active: proxy.mode === 'local' }" @click="proxy.setMode('local')">本地代理</button>
          <button class="seg-btn" :class="{ active: proxy.mode === 'global' }" @click="proxy.setMode('global')">全局代理</button>
        </div>
      </div>
      <div class="proxy-row">
        <span class="proxy-label">协议</span>
        <div class="seg">
          <button class="seg-btn" :class="{ active: proxy.proto === 'ss' }" @click="proxy.setProto('ss')">SS</button>
          <button class="seg-btn" :class="{ active: proxy.proto === 'reality' }" @click="proxy.setProto('reality')">Reality</button>
          <button class="seg-btn" :class="{ active: proxy.proto === 'hysteria2' }" @click="proxy.setProto('hysteria2')">Hysteria2</button>
        </div>
      </div>
      <div class="proxy-status" :class="{ connected: proxy.upstreamNode }">
        {{ proxy.upstreamNode ? `${proxy.mode === 'local' ? '本地代理' : '全局代理'} · ${protoName(proxy.proto)} · 127.0.0.1:7890` : '未设置上游' }}
      </div>
    </div>

    <div class="table-wrap">
      <table class="nodes-table">
        <thead>
          <tr>
            <th class="col-sel"><input type="checkbox" :checked="allSelected" @change="toggleSelectAll" title="全选" /></th>
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
            <td class="col-sel"><input type="checkbox" class="row-sel" :checked="selectedIds.has(n.resource_id)" @change="toggleSelect(n.resource_id)" /></td>
            <td class="col-idx">{{ i + 1 }}</td>
            <td><span class="type-badge type-vultr">Vultr</span></td>
            <td>{{ n.name || "Vultr 线路" }}</td>
            <td>{{ n.location || "—" }}</td>
            <td class="mono">{{ n.ip || "—" }}</td>
            <td class="mono">{{ n.domain || n.host || "—" }}</td>
            <td>
              <div class="node-check-status">
                <span class="status" :class="connectivityTests[n.resource_id]?.active ? 'pending' : (nodeStatus[n.resource_id]?.cls || 'pending')" :role="connectivityTests[n.resource_id]?.active ? 'status' : undefined">
                  {{ connectivityTests[n.resource_id]?.active ? `检测中 ${checkProgress(n)}` : (nodeStatus[n.resource_id]?.text || '未检测') }}
                </span>
                <div v-if="connectivityTests[n.resource_id]" class="check-protocols" aria-live="polite">
                  <span v-for="(item, p) in connectivityTests[n.resource_id].protocols" :key="p" class="check-chip" :class="item.state">
                    {{ protoName(p) }}：{{ item.state === 'checking' ? '检测中…' : (item.state === 'ok' ? `${item.ms}ms` : '不可达') }}
                  </span>
                </div>
              </div>
            </td>
            <td class="cell-muted">{{ (n.created_at || "").slice(0, 10) || "—" }}</td>
            <td :class="formatExpiry(n).cls">{{ formatExpiry(n).text }}</td>
            <td :class="switchInfo(n).cls">{{ switchInfo(n).text }}</td>
            <td :class="flowInfo(n).cls">{{ flowInfo(n).text }}</td>
            <td><input type="text" class="note-input" placeholder="备注" :value="n.note || ''" /></td>
            <td class="col-ops">
              <div class="row-ops">
                <button class="btn upstream-btn" :class="{ 'active-upstream': proxy.upstreamId === n.resource_id }" @click="proxy.toggleUpstream(n)">
                  {{ proxy.upstreamId === n.resource_id ? '取消上游' : '设为上游' }}
                </button>
                <button class="btn" :disabled="switching[n.resource_id]?.disabled || !!isSwitchDisabled(n)" @click="switchIp(n)">
                  {{ switching[n.resource_id]?.text || (isSwitchDisabled(n) || '切换IP') }}
                </button>
                <button class="btn" :disabled="switching[n.resource_id]?.checking" @click="checkNode(n)">
                  {{ switching[n.resource_id]?.checking ? `检测中 ${checkProgress(n)}` : '查联通性' }}
                </button>
                <button v-if="n.links && Object.keys(n.links).length" class="btn" @click="shareNode = n">复制节点</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <LogPanel />

    <ShareDialog v-if="shareNode" :node="shareNode" @close="shareNode = null" @show-qr="qr = $event" />
    <QrDialog v-if="qr" :name="qr.name" :link="qr.link" @close="qr = null" />
    <RegionModal v-if="regionNode" :node="regionNode" @pick="onRegionPick" @close="onRegionClose" />
    <BatchDialog v-if="batchModal" :nodes="batchPending" @close="batchModal = false" @confirm="doBatchSwitch" />
    <UpdateDialog ref="updateDialog" />
  </div>
</template>

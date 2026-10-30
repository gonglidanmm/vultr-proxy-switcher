<script setup>
import { computed, ref } from "vue";
import { copyText } from "../api/index.js";

const props = defineProps({ node: Object });
const emit = defineEmits(["close", "showQr"]);

// 把协议链接的 fragment（#xxx）替换成节点名称，作为节点标识（扫码/导入时显示）
function withName(link) {
  if (!link) return "";
  const name = (props.node?.name || "").trim();
  if (!name) return link;
  const hashIdx = link.indexOf("#");
  const base = hashIdx >= 0 ? link.slice(0, hashIdx) : link;
  return `${base}#${name}`;
}

const groups = computed(() => {
  const n = props.node || {};
  const host = n.host || n.domain || "";
  return [
    {
      key: "socks5",
      name: "SOCKS5",
      desc: "标准 SOCKS5 代理，带用户名密码认证",
      link: withName(n.links?.socks5),
      fields: [
        ["地址", host], ["端口", n.socks5?.port],
        ["用户名", n.socks5?.username], ["密码", n.socks5?.password],
      ],
    },
    {
      key: "ss",
      name: "Shadowsocks",
      desc: "SS 加密代理",
      link: withName(n.links?.ss),
      fields: [
        ["地址", host], ["端口", n.ss?.port],
        ["加密", n.ss?.method], ["密码", n.ss?.password],
      ],
    },
    {
      key: "reality",
      name: "Reality",
      desc: "VLESS + Reality（伪装 TLS，无需域名证书）",
      link: withName(n.links?.reality),
      fields: [
        ["地址", host], ["端口", n.reality?.port], ["UUID", n.reality?.uuid],
        ["公钥", n.reality?.public_key], ["Short ID", n.reality?.short_id],
        ["SNI", n.reality?.sni], ["Flow", n.reality?.flow], ["指纹", n.reality?.fp],
      ],
    },
    {
      key: "hysteria2",
      name: "Hysteria2",
      desc: "Hysteria2 高性能 QUIC 代理",
      link: withName(n.links?.hysteria2),
      fields: [
        ["地址", host], ["端口", n.hysteria2?.port],
        ["密码", n.hysteria2?.password], ["端口跳跃", n.hysteria2?.mport],
      ],
    },
  ];
});

const copiedKey = ref("");

async function doCopy(text, key) {
  if (text === undefined || text === null || text === "") return;
  const ok = await copyText(String(text));
  if (ok) {
    copiedKey.value = key;
    setTimeout(() => { if (copiedKey.value === key) copiedKey.value = ""; }, 1500);
  }
}

function showQr(g) {
  if (g.link) emit("showQr", { name: props.node?.name || g.name, link: g.link });
}
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')">
    <div class="modal share-modal">
      <div class="modal-head">
        <span class="modal-title">复制节点 — {{ node?.name || node?.host || "节点" }}</span>
        <button class="modal-close" title="关闭" @click="emit('close')">✕</button>
      </div>
      <div class="modal-body">
        <div v-for="g in groups" :key="g.key" class="share-group">
          <div class="share-group-head">
            <div class="share-group-title">{{ g.name }}</div>
            <div class="share-group-desc">{{ g.desc }}</div>
          </div>
          <div v-if="g.link" class="share-link-box">
            <code class="share-link-val">{{ g.link }}</code>
            <button class="btn" @click="doCopy(g.link, g.key + '-link')">
              {{ copiedKey === g.key + '-link' ? '已复制 ✓' : '复制链接' }}
            </button>
            <button class="btn" title="查看协议二维码" @click="showQr(g)">二维码</button>
          </div>
          <div v-for="(f, i) in g.fields" :key="i" class="share-row" v-show="f[1]">
            <div class="share-label-wrap">
              <span class="share-label">{{ f[0] }}</span>
            </div>
            <code class="share-val">{{ f[1] }}</code>
            <button class="btn" @click="doCopy(f[1], g.key + '-f' + i)">
              {{ copiedKey === g.key + '-f' + i ? '已复制 ✓' : '复制' }}
            </button>
          </div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn" @click="emit('close')">关闭</button>
      </div>
    </div>
  </div>
</template>

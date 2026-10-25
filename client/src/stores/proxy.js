import { defineStore } from "pinia";
import { invokeRust } from "../api/index.js";

const LOCAL_PROXY_PORT = 7890;
const TEST_PROXY_PORT = 7891;

export const useProxyStore = defineStore("proxy", {
  state: () => ({
    mode: "off", // off / local / global
    proto: "reality",
    upstreamNode: null, // 当前上游节点对象
    logs: [], // [{ts, type, text}]
  }),
  getters: {
    upstreamId: (s) => (s.upstreamNode ? s.upstreamNode.resource_id : null),
  },
  actions: {
    // 日志（模仿服务端回显，带时间戳）
    log(text, type = "info") {
      const ts = new Date().toTimeString().slice(0, 8);
      this.logs.push({ ts, type, text });
      // 限制日志条数，避免无限增长
      if (this.logs.length > 500) this.logs.splice(0, this.logs.length - 500);
    },
    clearLogs() {
      this.logs = [];
    },
    showMsg(text, type = "info") {
      const logType = type === "success" ? "ok" : type === "error" ? "err" : type === "warn" ? "warn" : "info";
      String(text).split("\n").forEach((l) => this.log(l, logType));
    },
    // 构建 sing-box 配置（测试/本地代理共用）
    buildSingBoxConfig(n, proto, listenPort = LOCAL_PROXY_PORT) {
      const host = (n.domain && String(n.domain).includes(".")) ? n.domain : (n.host || n.ip || "");
      const config = {
        log: { level: "error", timestamp: false },
        inbounds: [{ type: "mixed", tag: "mixed-in", listen: "127.0.0.1", listen_port: listenPort }],
        outbounds: [{ type: "direct", tag: "direct" }],
        dns: { servers: [{ type: "udp", tag: "dns-cn", server: "119.29.29.29" }] },
      };
      let upstream = null;
      if (proto === "socks5" && n.socks5) {
        upstream = { type: "socks", tag: "upstream", server: host, server_port: n.socks5.port, username: n.socks5.username || "", password: n.socks5.password || "" };
      } else if (proto === "ss" && n.ss) {
        upstream = { type: "shadowsocks", tag: "upstream", server: host, server_port: n.ss.port, method: n.ss.method || "aes-256-gcm", password: n.ss.password || "" };
      } else if (proto === "reality" && n.reality) {
        upstream = {
          type: "vless", tag: "upstream", server: host, server_port: n.reality.port,
          uuid: n.reality.uuid || "", flow: n.reality.flow || "xtls-rprx-vision",
          tls: {
            enabled: true,
            server_name: n.reality.sni || "www.cloudflare.com",
            utls: { enabled: true, fingerprint: n.reality.fp || "chrome" },
            reality: { enabled: true, public_key: n.reality.public_key || "", short_id: n.reality.short_id || "" },
          },
        };
      } else if (proto === "hysteria2" && n.hysteria2) {
        upstream = {
          type: "hysteria2", tag: "upstream", server: host, server_port: n.hysteria2.port,
          password: n.hysteria2.password || "",
          tls: { enabled: true, insecure: true },
        };
      }
      if (!upstream) throw new Error("该节点缺少所选协议的配置");
      upstream.domain_resolver = "dns-cn";
      config.outbounds.push(upstream);
      config.route = { final: "upstream" };
      return JSON.stringify(config);
    },
    // 测试节点各协议「真实转发」（不是只测端口通）——并发测，各协议用独立端口避免冲突
    // onProgress(p, ms)：每测完一个协议立即回调（用于逐协议实时回显）
    async testNodeProtocols(n, showDiagnostics = false, onProgress = null) {
      const protos = ["socks5", "ss", "reality", "hysteria2"];
      const results = {};
      // 动态端口基准：避免多节点并发测试时端口冲突（每台各用一段随机端口）
      const basePort = 20000 + Math.floor(Math.random() * 2000); // 20000~22000
      await Promise.all(protos.map(async (p, idx) => {
        const cfg = n[p];
        if (!cfg || !cfg.port) { results[p] = null; return; }
        const port = basePort + idx; // 每节点独立端口段，并发不冲突
        let ms = -1;
        // 轻微错峰启动：保留并发，但避免同时拉起多个 sing-box/curl 造成瞬时卡顿
        if (idx) await new Promise((resolve) => setTimeout(resolve, idx * 180));
        try {
          const configJson = this.buildSingBoxConfig(n, p, port);
          // 加超时保护：test_proxy 最坏 ~30 秒，35 秒兜底，避免 Rust 侧卡死导致按钮永久锁死
          ms = Math.round(await Promise.race([
            invokeRust("test_proxy", { configJson, testPort: port }),
            new Promise((_, rej) => setTimeout(() => rej(new Error("检测超时")), 35000)),
          ]));
          results[p] = ms;
        } catch (e) {
          if (showDiagnostics) this.log(`   [诊断] ${p} 真实转发测试失败: ${e}`, "warn");
          results[p] = -1;
        }
        if (onProgress) onProgress(p, results[p]);
      }));
      return results;
    },
    // 启动/停止本地代理
    async startLocalProxy(n) {
      const cfg = this.buildSingBoxConfig(n, this.proto);
      await invokeRust("start_proxy", { configJson: cfg });
    },
    async stopLocalProxy() {
      try { await invokeRust("stop_proxy"); } catch (_) {}
    },
    async setSystemProxy(enable) {
      try {
        await invokeRust("set_system_proxy", { enable, host: "127.0.0.1", port: LOCAL_PROXY_PORT });
      } catch (_) {}
    },
    // 设为上游（toggle）
    async toggleUpstream(n) {
      if (this.upstreamNode && this.upstreamNode.resource_id === n.resource_id) {
        this.upstreamNode = null;
        this.mode = "off";
        await this.stopLocalProxy();
        await this.setSystemProxy(false);
        return "off";
      }
      const order = ["ss", "reality", "hysteria2"];
      const protos = order.filter((p) => n[p] || (n.links && n.links[p]));
      if (!protos.length) return "no-proto";
      this.upstreamNode = n;
      if (!protos.includes(this.proto)) {
        this.proto = protos.includes("reality") ? "reality" : protos[0];
      }
      this.mode = "local";
      try {
        await this.startLocalProxy(n);
        await this.setSystemProxy(true);
        return "ok";
      } catch (e) {
        this.upstreamNode = null;
        this.mode = "off";
        await this.setSystemProxy(false);
        return "fail:" + (e.message || e);
      }
    },
    // 设置模式/协议
    async setMode(mode) {
      if (!this.upstreamNode && mode !== "off") return;
      this.mode = mode;
      await this.setSystemProxy(mode !== "off");
    },
    async setProto(proto) {
      this.proto = proto;
      if (this.upstreamNode) {
        try { await this.startLocalProxy(this.upstreamNode); } catch (_) {}
      }
    },
    reset() {
      this.mode = "off";
      this.proto = "reality";
      this.upstreamNode = null;
    },
  },
});

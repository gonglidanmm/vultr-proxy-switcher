import { defineStore } from "pinia";
import { api } from "../api/index.js";

export const useNodesStore = defineStore("nodes", {
  state: () => ({
    nodes: [],
    loading: false,
    error: "",
    expiry: "",
  }),
  actions: {
    async load() {
      this.loading = true;
      this.error = "";
      try {
        const data = await api("/me/nodes");
        this.nodes = data.nodes || [];
        // 到期：以最久的节点到期时间为准
        let maxExpiry = "";
        for (const n of this.nodes) {
          const e = (n.client_expiry || n.expiry || "").slice(0, 10);
          if (e && e > maxExpiry) maxExpiry = e;
        }
        this.expiry = maxExpiry;
      } catch (e) {
        this.error = e.message || "加载失败";
        throw e;
      } finally {
        this.loading = false;
      }
    },
    // 静默刷新：不显示 loading、不抛错（定时自动刷新用，避免界面闪烁）
    async silentLoad() {
      try {
        const data = await api("/me/nodes");
        this.nodes = data.nodes || [];
        let maxExpiry = "";
        for (const n of this.nodes) {
          const e = (n.client_expiry || n.expiry || "").slice(0, 10);
          if (e && e > maxExpiry) maxExpiry = e;
        }
        this.expiry = maxExpiry;
      } catch (_) {
        // 静默失败，保留旧数据
      }
    },
    // 退出登录时清空，避免切换账号后残留上一个账号的节点/测试信息
    reset() {
      this.nodes = [];
      this.expiry = "";
      this.error = "";
    },
  },
});

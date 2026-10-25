import { defineStore } from "pinia";
import { api, TOKEN_KEY, USER_KEY } from "../api/index.js";

export const useAuthStore = defineStore("auth", {
  state: () => ({
    token: localStorage.getItem(TOKEN_KEY) || "",
    user: JSON.parse(localStorage.getItem(USER_KEY) || "null"),
    loading: false,
    error: "",
  }),
  getters: {
    isLoggedIn: (s) => !!s.token,
  },
  actions: {
    async login(username, password) {
      this.loading = true;
      this.error = "";
      try {
        const data = await api("/login", "POST", { username, password }, false);
        this.token = data.token;
        this.user = data.user;
        localStorage.setItem(TOKEN_KEY, data.token);
        localStorage.setItem(USER_KEY, JSON.stringify(data.user));
      } catch (e) {
        this.error = e.message || "登录失败";
        throw e;
      } finally {
        this.loading = false;
      }
    },
    logout() {
      this.token = "";
      this.user = null;
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(USER_KEY);
    },
  },
});

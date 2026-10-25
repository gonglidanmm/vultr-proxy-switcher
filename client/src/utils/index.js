// 公共工具函数（从旧 main.js 迁移）
export const REGIONS = {
  "美国": ["atl", "ord", "dfw", "ewr", "lax", "mia", "sea", "sjc"],
  "日本": ["nrt", "itm"],
  "韩国": ["icn"],
  "新加坡": ["sgp"],
  "英国": ["lhr", "man"],
  "德国": ["fra"],
  "荷兰": ["ams"],
  "法国": ["cdg"],
  "加拿大": ["yto"],
  "澳大利亚": ["syd", "mel"],
  "印度": ["bom", "del"],
};

export const REGION_NAMES = {
  "atl": "亚特兰大", "ord": "芝加哥", "dfw": "达拉斯", "ewr": "纽约",
  "lax": "洛杉矶", "mia": "迈阿密", "sea": "西雅图", "sjc": "圣何塞", "hnl": "檀香山",
  "nrt": "东京", "itm": "大阪", "icn": "首尔", "sgp": "新加坡",
  "lhr": "伦敦", "man": "曼彻斯特", "fra": "法兰克福", "ams": "阿姆斯特丹",
  "cdg": "巴黎", "yto": "多伦多", "syd": "悉尼", "mel": "墨尔本",
  "bom": "孟买", "del": "德里",
};

export const STAGE_TEXT = {
  creating: "正在创建新机",
  destroying: "正在销毁旧机",
  booting: "新机已创建，正在启动中",
  installing: "新机运行中，正在下载安装代理软件",
  testing: "代理软件已就绪，正在检测端口",
  retrying: "端口检测失败，正在重建新机",
  done: "切换成功",
  failed: "切换失败",
};

export function protoName(p) {
  return { socks5: "SOCKS5", ss: "SS", reality: "Reality", hysteria2: "Hysteria2" }[p] || p;
}

export function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// 版本号比较：a > b 返回 1，a < b 返回 -1，相等返回 0
export function compareVersion(a, b) {
  const pa = String(a).split(".").map(Number);
  const pb = String(b).split(".").map(Number);
  for (let i = 0; i < 3; i++) {
    const x = pa[i] || 0, y = pb[i] || 0;
    if (x > y) return 1;
    if (x < y) return -1;
  }
  return 0;
}

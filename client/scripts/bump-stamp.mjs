// 构建后给 index.html 的静态资源加 ?v= 时间戳，供 Worker 解析 FRONTEND_STAMP，
// 客户端据此检测前端热更新（build-stamp 变化 → 自动 reload）。
import { readFileSync, writeFileSync } from "fs";

const stamp = String(Date.now());
let html = readFileSync("dist/index.html", "utf8");
html = html.replace(/src="\/main\.js"/, `src="/main.js?v=${stamp}"`);
html = html.replace(/href="\/style\.css"/, `href="/style.css?v=${stamp}"`);
writeFileSync("dist/index.html", html);
console.log(`bump-stamp: ${stamp}`);

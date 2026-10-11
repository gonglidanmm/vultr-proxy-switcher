# Vultr 代理切换器

跨平台（Windows / macOS）SOCKS5 代理切换器。用 Vultr 服务器做代理池，桌面 App 一键切换出口 IP。

## 原理

- 服务器端：Vultr 实例跑 `gost`，提供 SOCKS5（带账号密码）。
- 本地：App 在 `127.0.0.1:1080` 起一个本地转发代理，把流量转发到当前选中的服务器。
- **切换 = 点一下**：浏览器/软件永远填 `127.0.0.1:1080`，切换时无需改任何配置。

## 运行

```bash
uv sync
uv run python -m app.main
```

## 使用

1. **设置页**：填 Vultr API 密钥（my.vultr.com → Account → API，需打开 Enable API 并清空 IP 白名单）、代理账号密码、本地端口。
2. **服务器页**：`新建服务器` 自动购买+装代理；或 `手工添加` 已有服务器。
3. 点某台服务器的 **切换** 即把本地代理指向它；状态卡实时显示出口 IP 和延迟。

## 测试

```bash
uv run python scripts/test_localproxy.py
```

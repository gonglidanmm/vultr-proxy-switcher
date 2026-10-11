// cloud-init 脚本生成（移植自 Python build_gost_user_data）
// 输入 creds（全局凭据），输出 base64 的 cloud-init bash 脚本。

function b64utf8(s) {
  // UTF-8 安全的 base64
  const bytes = new TextEncoder().encode(s);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

export function buildGostUserData(creds, domain = "", cert = null, workerUrl = "", certToken = "") {
  const c = creds || {};
  const user = c.proxy_user || "";
  const password = c.proxy_pass || "";
  const port = parseInt(c.proxy_port || "1080", 10);
  const ss_password = c.ss_password || "";
  const ss_port = parseInt(c.ss_port || "45678", 10);
  const root_password = c.root_password || "";
  const reality_uuid = c.reality_uuid || "";
  const reality_private_key = c.reality_private_key || "";
  const reality_short_id = c.reality_short_id || "";
  const reality_sni = c.reality_sni || "www.microsoft.com";
  const reality_port = parseInt(c.reality_port || "443", 10);
  const hysteria_password = c.hysteria_password || "";
  const hysteria_port = parseInt(c.hysteria_port || "8443", 10);
  const hysteria_mport = c.hysteria_mport || "20000-20100";
  const hop_ports = hysteria_mport.replace(/-/g, ":");

  // listeners
  let listeners = `-L socks5://${user}:${password}@:${port}`;
  if (ss_password) {
    listeners += ` -L ss://aes-256-gcm:${ss_password}@:${ss_port}`;
  }

  const root_line = root_password ? `echo 'root:${root_password}' | chpasswd` : "";

  // xray
  let xray_section = "";
  if (reality_uuid && reality_private_key) {
    const cfg = JSON.stringify({
      log: { loglevel: "warning" },
      inbounds: [
        {
          listen: "0.0.0.0",
          port: reality_port,
          protocol: "vless",
          settings: {
            clients: [{ id: reality_uuid, flow: "xtls-rprx-vision" }],
            decryption: "none",
          },
          streamSettings: {
            network: "tcp",
            security: "reality",
            realitySettings: {
              dest: `${reality_sni}:443`,
              xver: 0,
              serverNames: [reality_sni],
              privateKey: reality_private_key,
              shortIds: [reality_short_id],
            },
          },
          sniffing: { enabled: true, destOverride: ["http", "tls", "quic"] },
        },
      ],
      outbounds: [
        { protocol: "freedom", tag: "direct" },
        { protocol: "blackhole", tag: "block" },
      ],
    });
    const cfg_b64 = b64utf8(cfg);
    xray_section = `
# Xray (VLESS+Reality)
if [ ! -x /usr/local/bin/xray ]; then
  wget -q -O /tmp/xray.zip https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip
  unzip -q -o /tmp/xray.zip -d /usr/local/bin/
  chmod +x /usr/local/bin/xray
fi
mkdir -p /usr/local/etc/xray
echo '${cfg_b64}' | base64 -d > /usr/local/etc/xray/config.json
cat > /etc/systemd/system/xray.service <<'XEOF'
[Unit]
Description=Xray Service
After=network.target
[Service]
ExecStart=/usr/local/bin/xray run -config /usr/local/etc/xray/config.json
Restart=on-failure
[Install]
WantedBy=multi-user.target
XEOF
systemctl daemon-reload
systemctl enable xray
systemctl restart xray
`;
  }

  // hysteria
  let ufw_hysteria = "";
  let hysteria_section = "";
  if (hysteria_password) {
    ufw_hysteria = `ufw allow ${hysteria_port}/udp 2>/dev/null || true\nufw allow ${hop_ports}/udp 2>/dev/null || true\n`;
    const hop_rule = `iptables -t nat -C PREROUTING -p udp --dport ${hop_ports} -j REDIRECT --to-ports ${hysteria_port} 2>/dev/null || iptables -t nat -A PREROUTING -p udp --dport ${hop_ports} -j REDIRECT --to-ports ${hysteria_port}`;
    hysteria_section = `
# Hysteria2
if [ ! -x /usr/local/bin/hysteria ]; then
  wget -q -O /usr/local/bin/hysteria https://github.com/apernet/hysteria/releases/download/app/v2.6.1/hysteria-linux-amd64
  chmod +x /usr/local/bin/hysteria
fi
mkdir -p /etc/hysteria
${cert ? `# 复用已签发证书，直接部署（切换 IP 不重签，避免 Let's Encrypt 限流）
cat > /etc/hysteria/server.crt <<'CERTEOF'
${cert.fullchain}
CERTEOF
cat > /etc/hysteria/server.key <<'KEYEOF'
${cert.key}
KEYEOF
` : `openssl req -x509 -nodes -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -keyout /etc/hysteria/server.key -out /etc/hysteria/server.crt -days 3650 -subj '/CN=hysteria' 2>/dev/null`}
cat > /etc/hysteria/config.yaml <<HYEOF
listen: :${hysteria_port}
tls:
  cert: /etc/hysteria/server.crt
  key: /etc/hysteria/server.key
auth:
  type: password
  password: ${hysteria_password}
HYEOF
cat > /etc/systemd/system/hysteria.service <<'HYSEOF'
[Unit]
Description=Hysteria2
After=network.target
[Service]
ExecStartPre=/bin/sh -c '${hop_rule}'
ExecStart=/usr/local/bin/hysteria server -c /etc/hysteria/config.yaml
Restart=always
RestartSec=3
LimitNOFILE=65535
[Install]
WantedBy=multi-user.target
HYSEOF
systemctl daemon-reload
systemctl enable hysteria
systemctl restart hysteria
${domain && !cert ? `# 后台签正式域名证书（等 DNS 传播后自动执行，不依赖管理端）
cat > /usr/local/bin/sign-cert.sh <<'CERTEOF'
#!/bin/bash
DOMAIN="$1"
# 确保 acme.sh 装到 /root/.acme.sh（nohup 后台运行时 HOME 可能不是 /root，导致装到 /.acme.sh）
export HOME=/root
# 等 DNS 传播（Cloudflare proxied 下 getent 返回代理 IP，无法用 IP 判断，固定等 90 秒）
sleep 90
# 安装 acme.sh（重试 3 次，保留错误输出，避免网络抖动导致安装失败）
if [ ! -f /root/.acme.sh/acme.sh ]; then
  for i in 1 2 3; do
    curl -m 120 -s https://get.acme.sh | sh -s email=admin@yduas.cc && break
    sleep 10
  done
fi
# 签正式证书（standalone 需 80 端口，先放行；重试 3 次）
ufw allow 80/tcp 2>/dev/null || true
for i in 1 2 3; do
  /root/.acme.sh/acme.sh --issue -d "$DOMAIN" --standalone --keylength ec-256 --server letsencrypt 2>&1 | tail -3
  [ -f "/root/.acme.sh/\${DOMAIN}_ecc/fullchain.cer" ] && break
  sleep 20
done
# 部署到 hysteria 并重启
/root/.acme.sh/acme.sh --install-cert -d "$DOMAIN" --ecc \\
  --key-file /etc/hysteria/server.key \\
  --fullchain-file /etc/hysteria/server.crt \\
  --reloadcmd "systemctl restart hysteria" 2>&1 | tail -3
# 回传证书到 Worker 缓存（下次切换 IP 复用，避免频繁签发触发限流）
cat > /tmp/cert-save.py <<'PYEOF'
import json, sys, urllib.request
domain = sys.argv[1]
fullchain = open(sys.argv[2]).read()
key = open(sys.argv[3]).read()
data = json.dumps({"domain": domain, "fullchain": fullchain, "key": key}).encode()
req = urllib.request.Request("${workerUrl}/admin/cert-save", data=data, method="POST", headers={"Content-Type": "application/json", "X-Cert-Token": "${certToken}"})
try:
    urllib.request.urlopen(req, timeout=30)
except Exception:
    pass
PYEOF
python3 /tmp/cert-save.py "\$DOMAIN" "/root/.acme.sh/\${DOMAIN}_ecc/fullchain.cer" "/root/.acme.sh/\${DOMAIN}_ecc/\${DOMAIN}.key"
CERTEOF
chmod +x /usr/local/bin/sign-cert.sh
nohup /usr/local/bin/sign-cert.sh '${domain}' > /var/log/sign-cert.log 2>&1 &
` : ``}
`;
  }

  const script = `#!/bin/bash
set -e
apt-get update -y >/dev/null 2>&1 || true
${root_line}
ufw allow ${port}/tcp 2>/dev/null || true
ufw allow ${port}/udp 2>/dev/null || true
ufw allow ${ss_port}/tcp 2>/dev/null || true
ufw allow ${ss_port}/udp 2>/dev/null || true
ufw allow ${reality_port}/tcp 2>/dev/null || true
${ufw_hysteria}ufw allow 22/tcp 2>/dev/null || true
systemctl enable --now ssh 2>/dev/null || systemctl enable --now sshd 2>/dev/null || true
ufw --force enable 2>/dev/null || true
systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || true
if [ ! -x /usr/local/bin/gost ]; then
  wget -q -O /usr/local/bin/gost.gz https://github.com/ginuerzh/gost/releases/download/v2.11.5/gost-linux-amd64-2.11.5.gz
  gunzip -f /usr/local/bin/gost.gz
  chmod +x /usr/local/bin/gost
fi
cat > /etc/systemd/system/gost.service <<'GEOF'
[Unit]
Description=gost proxy (socks5 + shadowsocks)
After=network.target
[Service]
ExecStart=/usr/local/bin/gost ${listeners}
Restart=always
RestartSec=3
LimitNOFILE=65535
[Install]
WantedBy=multi-user.target
GEOF
systemctl daemon-reload
systemctl enable gost
systemctl restart gost
${xray_section}
${hysteria_section}
`;

  return b64utf8(script);
}

#!/bin/sh
# Tailscale 一键安装（OpenWrt/iStoreOS/QWRT 通用）
# 用法: sh install_tailscale.sh <你的AuthKey>
# 自动检测架构 + 下载对应版本 + 安装 + 开机自启 + 登录 + 防火墙 + dropbear
# 下载约 3 分钟（直连官方源慢，非被墙），请耐心等待

AUTHKEY="$1"
if [ -z "$AUTHKEY" ]; then
  echo "用法: sh $0 <tskey-auth-xxx>"
  exit 1
fi

# 1. 检测架构 → Tailscale 命名
ARCH=$(uname -m)
case "$ARCH" in
  x86_64|amd64)  TS_ARCH="amd64" ;;
  aarch64|arm64) TS_ARCH="arm64" ;;
  armv7l|armv6l) TS_ARCH="arm" ;;
  mips)          TS_ARCH="mips" ;;
  mipsel)        TS_ARCH="mipsle" ;;
  mips64)        TS_ARCH="mips64" ;;
  i386|i686)     TS_ARCH="386" ;;
  *) echo "不支持的架构: $ARCH"; exit 1 ;;
esac
echo "[1/7] 架构: $ARCH -> Tailscale $TS_ARCH"

# 2. 获取最新版本号（失败则 fallback 1.104.2）
VER=$(wget -qO- --timeout=15 https://pkgs.tailscale.com/stable/ 2>/dev/null | grep -oE 'tailscale_[0-9]+\.[0-9]+\.[0-9]+_'"$TS_ARCH"'\.tgz' | head -1 | sed 's/tailscale_//;s/_'"$TS_ARCH"'\.tgz//')
[ -z "$VER" ] && VER="1.104.2"
echo "[2/7] 版本: $VER"

# 3. 下载（慢，约 3 分钟，长超时 + 重试）
echo "[3/7] 下载中（约 3 分钟，请勿中断）..."
wget -q --timeout=180 --tries=3 -O /tmp/tailscale.tgz "https://pkgs.tailscale.com/stable/tailscale_${VER}_${TS_ARCH}.tgz" || { echo "下载失败"; exit 1; }

# 4. 解压安装
cd /tmp && tar xzf tailscale.tgz
TS_DIR=$(ls -d tailscale_*_${TS_ARCH} | head -1)
mkdir -p /var/lib/tailscale /var/run/tailscale
cp "$TS_DIR/tailscale" "$TS_DIR/tailscaled" /usr/sbin/
chmod +x /usr/sbin/tailscale /usr/sbin/tailscaled
echo "[4/7] 已安装到 /usr/sbin/"

# 5. 开机自启脚本
cat > /etc/init.d/tailscale <<'EOF'
#!/bin/sh /etc/rc.common
START=90
STOP=10
start() {
    mkdir -p /var/lib/tailscale /var/run/tailscale
    /usr/sbin/tailscaled --state=/var/lib/tailscale/tailscaled.state --socket=/var/run/tailscale/tailscaled.sock >/var/log/tailscaled.log 2>&1 &
}
stop() {
    killall tailscaled 2>/dev/null
    killall tailscale 2>/dev/null
}
EOF
chmod +x /etc/init.d/tailscale
/etc/init.d/tailscale enable
echo "[5/7] 开机自启已配置"

# 6. 启动 + 登录
/usr/sbin/tailscaled --state=/var/lib/tailscale/tailscaled.state --socket=/var/run/tailscale/tailscaled.sock >/var/log/tailscaled.log 2>&1 &
sleep 3
/usr/sbin/tailscale --socket=/var/run/tailscale/tailscaled.sock up --authkey="$AUTHKEY" --netfilter-mode=off --accept-dns=false
sleep 3
echo "[6/7] 已登录"

# 7. 防火墙 + dropbear（关键）
uci set network.tailscale=interface
uci set network.tailscale.proto='none'
uci set network.tailscale.device='tailscale0'
uci commit network
# dropbear 删 Interface 监听所有接口（千万别写 lan tailscale，会挂）
uci delete dropbear.@dropbear[0].Interface 2>/dev/null
uci commit dropbear
/etc/init.d/dropbear restart 2>/dev/null
sleep 2
iptables -I zone_tailscale_input -i tailscale0 -j ACCEPT 2>/dev/null
echo "[7/7] 防火墙 + dropbear 已配置"

echo ""
echo "================ 完成 ================"
/usr/sbin/tailscale --socket=/var/run/tailscale/tailscaled.sock status 2>&1 | head -5
echo ""
echo "Tailscale IP:"
/usr/sbin/tailscale --socket=/var/run/tailscale/tailscaled.sock ip 2>&1
echo "====================================="

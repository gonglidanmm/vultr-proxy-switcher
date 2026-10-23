#!/bin/sh
# Tailscale 修复脚本：解决「重启后掉线」
# 根因：state 放在 /var/lib（tmpfs 内存盘，重启清空）+ 防火墙用临时 iptables（重启丢失）
# 修复：state 挪到 /etc/tailscale（持久化）+ 防火墙改 uci zone（持久化）+ 重新登录
# 用法: sh fix_tailscale.sh <你的AuthKey>

AUTHKEY="$1"
[ -z "$AUTHKEY" ] && { echo "用法: sh $0 <tskey-auth-xxx>"; exit 1; }

# 1. 停掉当前 tailscaled（state 已丢，是空登录态）
killall tailscaled 2>/dev/null
sleep 1

# 2. 建持久化目录（/etc 是 overlay，重启不丢）
mkdir -p /etc/tailscale

# 3. 用持久化 state 启动 + 重新登录
/usr/sbin/tailscaled --state=/etc/tailscale/tailscaled.state --socket=/var/run/tailscale/tailscaled.sock >/var/log/tailscaled.log 2>&1 &
sleep 3
/usr/sbin/tailscale --socket=/var/run/tailscale/tailscaled.sock up --authkey="$AUTHKEY" --netfilter-mode=off --accept-dns=false
sleep 3

# 4. 更新开机自启脚本（state 用持久化路径）
cat > /etc/init.d/tailscale <<'EOF'
#!/bin/sh /etc/rc.common
START=90
STOP=10
start() {
    mkdir -p /var/run/tailscale
    /usr/sbin/tailscaled --state=/etc/tailscale/tailscaled.state --socket=/var/run/tailscale/tailscaled.sock >/var/log/tailscaled.log 2>&1 &
}
stop() {
    killall tailscaled 2>/dev/null
    killall tailscale 2>/dev/null
}
EOF
chmod +x /etc/init.d/tailscale
/etc/init.d/tailscale enable
echo "[4/6] 开机自启已更新（state 持久化到 /etc/tailscale）"

# 5. 防火墙持久化（uci zone，替代会丢的临时 iptables）
uci set network.tailscale=interface
uci set network.tailscale.proto='none'
uci set network.tailscale.device='tailscale0'
uci commit network

# 删掉已存在的 tailscale zone（避免重复）
FW_TS=$(uci show firewall 2>/dev/null | grep -E "name='tailscale'" | head -1 | cut -d'.' -f2 | cut -d'=' -f1)
[ -n "$FW_TS" ] && uci delete firewall.$FW_TS

uci add firewall zone
uci set firewall.@zone[-1].name='tailscale'
uci set firewall.@zone[-1].network='tailscale'
uci set firewall.@zone[-1].input='ACCEPT'
uci set firewall.@zone[-1].output='ACCEPT'
uci set firewall.@zone[-1].forward='ACCEPT'
uci commit firewall
/etc/init.d/firewall restart 2>/dev/null
sleep 2
echo "[5/6] 防火墙已改持久化 uci zone"

# 6. 验证
echo "[6/6] 完成，状态："
tailscale --socket=/var/run/tailscale/tailscaled.sock status 2>&1 | head -6
echo ""
echo "Tailscale IP:"
tailscale --socket=/var/run/tailscale/tailscaled.sock ip 2>&1

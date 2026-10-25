#!/bin/sh
# YY 网段 bridge 手动修复（绕过 netifd DEVICE_CLAIM_FAILED race condition）
# 修复 radio2 的 YY09~16 bridge，并写入 rc.local 持久化
# IP: YY09->192.168.49.1 ... YY16->192.168.56.1

fix_radio2() {
  local i idx NET
  for i in 9 10 11 12 13 14 15 16; do
    NET=$(printf "YY%02d" $i)
    idx=$((i-9))
    ip link del ${NET}-AP 2>/dev/null
    ip link add name ${NET}-AP type bridge 2>/dev/null
    ip link set phy2-ap$idx nomaster 2>/dev/null
    ip link set phy2-ap$idx master ${NET}-AP 2>/dev/null
    ip link set ${NET}-AP up 2>/dev/null
    ip addr add 192.168.$((40+i)).1/24 dev ${NET}-AP 2>/dev/null
  done
}

# 执行修复
fix_radio2
sleep 2

echo "=== 网段数 ==="
ip addr show 2>/dev/null | grep -cE "inet 192.168.(4[1-9]|5[0-6])\.1"
ip addr show 2>/dev/null | grep -E "inet 192.168.(4[1-9]|5[0-6])\.1" | awk '{print $2}' | sort

#!/bin/sh
# YY 网段开机自动修复 DEVICE_CLAIM_FAILED（netifd 竞态条件）
# 方法：检测接口 down 的网段，ifdown + 删 bridge + ifup 让 netifd 重新声明
# （VAP 已就绪后重新 claim 必定成功，无需手动桥接/配 IP/补 dhcp-range）

sleep 20  # 等 netifd + hostapd 完全初始化

for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16; do
  NET=$(printf "YY%02d" $i)
  # 接口已 up（无错误）则跳过
  if ubus call network.interface status "{\"interface\":\"$NET\"}" 2>/dev/null | grep -q '"up": true'; then
    continue
  fi
  # down + 删 bridge + up，让 netifd 重新声明（VAP 已就绪，claim 成功）
  ifdown $NET 2>/dev/null
  ip link del ${NET}-AP 2>/dev/null
  sleep 1
  ifup $NET 2>/dev/null
done

exit 0

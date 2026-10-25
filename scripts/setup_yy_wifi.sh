#!/bin/sh
# YukiWrt 16 个 YY WiFi + 网段配置（ImmortalWrt 24.10 DSA 架构）
# radio0(5G ch149): YY-01~08, radio2(5G ch36): YY-09~16, radio1(2.4G)不动
# 网段 192.168.41.1 ~ 192.168.56.1, 密码 88888888, 绑定 lan（不隔离）

PASSWD="88888888"

# 幂等清理旧的 YY 配置
for w in $(uci show wireless 2>/dev/null | grep -E "ssid='YY-" | cut -d. -f2 | cut -d= -f1); do uci delete wireless.$w; done
for n in $(uci show network 2>/dev/null | grep -E "^network\.YY" | cut -d= -f1 | cut -d. -f2); do uci delete network.$n; done
for d in $(uci show dhcp 2>/dev/null | grep -E "^dhcp\.YY" | cut -d= -f1 | cut -d. -f2); do uci delete dhcp.$d; done

set_vap() {
  local name="$1" ssid="$2" dev="$3" net="$4"
  uci set wireless.$name=wifi-iface
  uci set wireless.$name.device="$dev"
  uci set wireless.$name.mode='ap'
  uci set wireless.$name.ssid="$ssid"
  uci set wireless.$name.encryption='psk2'
  uci set wireless.$name.key="$PASSWD"
  uci set wireless.$name.network="$net"
}

# radio0 的 VAP：YY-01~08
set_vap yy01 "YY-01" radio0 YY01
set_vap yy02 "YY-02" radio0 YY02
set_vap yy03 "YY-03" radio0 YY03
set_vap yy04 "YY-04" radio0 YY04
set_vap yy05 "YY-05" radio0 YY05
set_vap yy06 "YY-06" radio0 YY06
set_vap yy07 "YY-07" radio0 YY07
set_vap yy08 "YY-08" radio0 YY08

# radio2 的 VAP：YY-09~16
set_vap yy09 "YY-09" radio2 YY09
set_vap yy10 "YY-10" radio2 YY10
set_vap yy11 "YY-11" radio2 YY11
set_vap yy12 "YY-12" radio2 YY12
set_vap yy13 "YY-13" radio2 YY13
set_vap yy14 "YY-14" radio2 YY14
set_vap yy15 "YY-15" radio2 YY15
set_vap yy16 "YY-16" radio2 YY16

# 网段接口 + device(bridge) + DHCP
for i in $(seq 1 16); do
  NET=$(printf "YY%02d" $i)
  IP_OCT=$((40 + i))  # 41~56

  # 接口
  uci set network.$NET=interface
  uci set network.$NET.proto='static'
  uci set network.$NET.device="$NET-AP"
  uci set network.$NET.ipaddr="192.168.$IP_OCT.1"
  uci set network.$NET.netmask='255.255.255.0'

  # device（bridge）
  uci set network.${NET}_dev=device
  uci set network.${NET}_dev.name="$NET-AP"
  uci set network.${NET}_dev.type='bridge'

  # DHCP
  uci set dhcp.$NET=dhcp
  uci set dhcp.$NET.interface="$NET"
  uci set dhcp.$NET.start='100'
  uci set dhcp.$NET.limit='150'
  uci set dhcp.$NET.leasetime='12h'
done

# 防火墙：并入 lan zone（不隔离）
LAN_NET=$(uci get firewall.@zone[0].network 2>/dev/null)
NNETS=""
for i in $(seq -w 1 16); do NNETS="$NNETS YY$i"; done
uci set firewall.@zone[0].network="lan$NNETS"

uci commit wireless
uci commit network
uci commit dhcp
uci commit firewall

echo "=== 应用配置 ==="
wifi reload
/etc/init.d/network reload
/etc/init.d/dnsmasq restart

echo ""
echo "=== SSID 列表 ==="
uci show wireless 2>/dev/null | grep -E "ssid='YY-" | sed "s/.*ssid=//" | sort
echo "=== 网段 IP ==="
uci show network 2>/dev/null | grep -E "YY[0-9]+\.ipaddr" | sed "s/.*ipaddr=//" | sort

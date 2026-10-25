#!/bin/sh
# 批量创建 16 个 KK WiFi + 网段 + 防火墙 + DHCP（TK 养号隔离）
# KK-01~08 → wifi0(5G ch149)，KK-09~16 → wifi2(5G ch36)，wifi1(2.4G)不动
# 网段 30.30.31.1 ~ 30.30.46.1，密码 88888888
# 幂等：重复执行先删旧 kk 配置

PASSWD="88888888"

# 幂等清理
for w in $(uci show wireless 2>/dev/null | grep -E "ssid='KK-" | cut -d. -f2 | cut -d= -f1); do uci delete wireless.$w; done
for n in $(uci show network 2>/dev/null | grep -E "^network\.kk" | cut -d= -f1 | cut -d. -f2); do uci delete network.$n; done
for z in $(uci show firewall 2>/dev/null | grep -E "name='kk" | cut -d. -f2 | cut -d= -f1); do uci delete firewall.$z; done
for d in $(uci show dhcp 2>/dev/null | grep -E "^dhcp\.kk" | cut -d= -f1 | cut -d. -f2); do uci delete dhcp.$d; done

for i in $(seq 1 16); do
  SSID=$(printf "KK-%02d" $i)
  NET=$(printf "kk%02d" $i)
  IP_OCT=$((30 + i))  # 31~46

  [ $i -le 8 ] && WIFI_DEV="wifi0" || WIFI_DEV="wifi2"

  # 1. 无线接口（VAP）
  uci add wireless wifi-iface
  uci set wireless.@wifi-iface[-1].device="$WIFI_DEV"
  uci set wireless.@wifi-iface[-1].mode='ap'
  uci set wireless.@wifi-iface[-1].ssid="$SSID"
  uci set wireless.@wifi-iface[-1].encryption='psk2'
  uci set wireless.@wifi-iface[-1].key="$PASSWD"
  uci set wireless.@wifi-iface[-1].network="$NET"

  # 2. 网络接口（静态 IP）
  uci set network.$NET=interface
  uci set network.$NET.proto='static'
  uci set network.$NET.ipaddr="30.30.$IP_OCT.1"
  uci set network.$NET.netmask='255.255.255.0'

  # 3. 防火墙 zone（入站拒绝 + 转发 + NAT）
  uci add firewall zone
  uci set firewall.@zone[-1].name="$NET"
  uci set firewall.@zone[-1].network="$NET"
  uci set firewall.@zone[-1].input='REJECT'
  uci set firewall.@zone[-1].output='ACCEPT'
  uci set firewall.@zone[-1].forward='ACCEPT'
  uci set firewall.@zone[-1].masq='1'

  # 4. DHCP（下发公共 DNS，避开入站拒绝挡路由 DNS）
  uci set dhcp.$NET=dhcp
  uci set dhcp.$NET.interface="$NET"
  uci set dhcp.$NET.start='100'
  uci set dhcp.$NET.limit='150'
  uci set dhcp.$NET.leasetime='12h'
  uci set dhcp.$NET.dhcp_option='6,223.5.5.5,119.29.29.29'
done

uci commit wireless
uci commit network
uci commit firewall
uci commit dhcp

echo "=== 应用配置 ==="
wifi reload
/etc/init.d/network reload
/etc/init.d/firewall restart
/etc/init.d/dnsmasq restart

echo ""
echo "=== SSID 列表 ==="
uci show wireless 2>/dev/null | grep -E "ssid='KK-" | sed "s/.*ssid=//" | sort
echo "=== 网段 IP 列表 ==="
uci show network 2>/dev/null | grep -E "kk[0-9]+\.ipaddr" | sed "s/.*ipaddr=//" | sort

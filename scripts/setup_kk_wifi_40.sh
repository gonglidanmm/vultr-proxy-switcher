#!/bin/sh
# 批量创建 16 个 KK WiFi + 网段 + DHCP（40.40.40.1，QWRT 21.02 ipq60xx）
# KK-01~08 → wifi0(5G ch149)，KK-09~16 → wifi2(5G ch36)，wifi1(2.4G)不动
# 网段 40.40.41.1 ~ 40.40.56.1，密码 88888888，并入 lan（不隔离）
# 关键：VAP 必须用命名 section（ath0~ath27），不能匿名——否则 wifi 脚本写 state 失败、netifd NO_DEVICE

PASSWD="88888888"

# 幂等清理旧的 KK VAP + 网段 + DHCP
for w in $(uci show wireless 2>/dev/null | grep -E "ssid='KK-" | cut -d. -f2 | cut -d= -f1); do uci delete wireless.$w; done
for n in $(uci show network 2>/dev/null | grep -E "^network\.kk" | cut -d= -f1 | cut -d. -f2); do uci delete network.$n; done
for d in $(uci show dhcp 2>/dev/null | grep -E "^dhcp\.kk" | cut -d= -f1 | cut -d. -f2); do uci delete dhcp.$d; done

set_vap() {
  local name="$1" ssid="$2" dev="$3" net="$4"
  uci set wireless.$name=wifi-iface
  uci set wireless.$name.device="$dev"
  uci set wireless.$name.mode='ap'
  uci set wireless.$name.ssid="$ssid"
  uci set wireless.$name.encryption='psk2'
  uci set wireless.$name.key="$PASSWD"
  uci set wireless.$name.network="$net"
  uci set wireless.$name.wmm='1'
  uci set wireless.$name.rrm='1'
  uci set wireless.$name.qbssload='1'
}

# wifi0 的 VAP：ath0 ~ ath07（KK-01~08）
set_vap ath0  "KK-01" wifi0 kk01
set_vap ath01 "KK-02" wifi0 kk02
set_vap ath02 "KK-03" wifi0 kk03
set_vap ath03 "KK-04" wifi0 kk04
set_vap ath04 "KK-05" wifi0 kk05
set_vap ath05 "KK-06" wifi0 kk06
set_vap ath06 "KK-07" wifi0 kk07
set_vap ath07 "KK-08" wifi0 kk08

# wifi2 的 VAP：ath2, ath21~ath27（KK-09~16）
set_vap ath2  "KK-09" wifi2 kk09
set_vap ath21 "KK-10" wifi2 kk10
set_vap ath22 "KK-11" wifi2 kk11
set_vap ath23 "KK-12" wifi2 kk12
set_vap ath24 "KK-13" wifi2 kk13
set_vap ath25 "KK-14" wifi2 kk14
set_vap ath26 "KK-15" wifi2 kk15
set_vap ath27 "KK-16" wifi2 kk16

# 网络接口（静态 IP，bridge）
for i in $(seq 1 16); do
  NET=$(printf "kk%02d" $i)
  IP_OCT=$((40 + i))  # 41~56
  uci set network.$NET=interface
  uci set network.$NET.type='bridge'
  uci set network.$NET.proto='static'
  uci set network.$NET.ipaddr="40.40.$IP_OCT.1"
  uci set network.$NET.netmask='255.255.255.0'
done

# DHCP（公共 DNS）
for i in $(seq 1 16); do
  NET=$(printf "kk%02d" $i)
  uci set dhcp.$NET=dhcp
  uci set dhcp.$NET.interface="$NET"
  uci set dhcp.$NET.start='100'
  uci set dhcp.$NET.limit='150'
  uci set dhcp.$NET.leasetime='12h'
  uci set dhcp.$NET.dhcp_option='6,223.5.5.5,119.29.29.29'
done

# 防火墙并入 lan（不隔离）
NNETS=""
for i in $(seq 1 16); do NNETS="$NNETS $(printf "kk%02d" $i)"; done
uci set firewall.@zone[0].network="lan$NNETS"

uci commit wireless
uci commit network
uci commit dhcp
uci commit firewall

echo "=== 应用 ==="
wifi reload
/etc/init.d/network reload

echo "=== 网段检查 ==="
ip addr show 2>/dev/null | grep -E "inet 40.40.(4[1-9]|5[0-6])\.1" | awk '{print $2}' | sort

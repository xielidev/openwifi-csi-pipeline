#!/bin/sh
# ap_up_5g.sh - one-shot: bring up the openwifi 5G AP (ch36/5180MHz) with
# +78kHz CFO compensation (scaled from +37kHz @ 2.4G by 15.2ppm) and NAT
# so clients can reach the internet.
#   [1/4] wgd.sh            : load FPGA bitstream + wireless drivers
#   [2/4] net setup         : sdr0 IP + isc-dhcp-server + agc_settings
#   [3/4] ap_cfo_start_5g.sh: hostapd 5G (ch36) + +78kHz on RX+TX LO
#   [4/4] NAT (MASQUERADE via iptables-legacy) + ensure DHCP
#
# Run as root. Safe to re-run (LO idempotent, hostapd auto-(re)started by
# ap_cfo_start_5g.sh when needed).
cd /root/openwifi || { echo "openwifi dir not found"; exit 1; }

echo "== [1/4] wgd.sh (FPGA + drivers) =="
./wgd.sh
echo "wgd    rc=$?"

echo "== [2/4] sdr0 IP + DHCP + AGC =="
killall hostapd 2>/dev/null || true
ifconfig sdr0 192.168.13.1
rm -f /var/run/dhcpd.pid
sleep 1
service isc-dhcp-server restart
./agc_settings.sh 1

echo "== [3/4] hostapd 5G (ch36) + +78kHz RX/TX LO offset =="
./ap_cfo_start_5g.sh
echo "cfo    rc=$?"

echo "== [4/4] iptables NAT + DHCP =="
# On this box 'iptables' defaults to the broken nf_tables backend, so use legacy.
if ! iptables-legacy -t nat -C POSTROUTING -o eth0 -j MASQUERADE 2>/dev/null; then
  iptables-legacy -t nat -A POSTROUTING -o eth0 -j MASQUERADE
fi
echo "ip_forward=$(cat /proc/sys/net/ipv4/ip_forward)"
iptables-legacy -t nat -L POSTROUTING -v | grep -q MASQUERADE && echo "MASQUERADE OK"
service isc-dhcp-server restart >/dev/null 2>&1 || true
pgrep -x dhcpd >/dev/null && echo "dhcpd running" || echo "dhcpd NOT running"

echo "== done. RX/TX LO should be 5180078000 =="
cat /sys/bus/iio/devices/iio:device1/out_altvoltage0_RX_LO_frequency
cat /sys/bus/iio/devices/iio:device1/out_altvoltage1_TX_LO_frequency

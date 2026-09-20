#!/bin/sh
# ap_cfo_start_5g.sh - CFO compensation for 5G ch36 (5180 MHz).
# +37kHz @ 2.437GHz is ~15.2ppm -> @5.18GHz it is ~+78.6kHz.
IIO=/sys/bus/iio/devices/iio:device1
NOM=5180000000
OFF=78000
APPLIED=$((NOM + OFF))
set_lo() { echo "$1" > $IIO/out_altvoltage0_RX_LO_frequency; echo "$1" > $IIO/out_altvoltage1_TX_LO_frequency; }
cur=$(cat $IIO/out_altvoltage0_RX_LO_frequency 2>/dev/null || true)
case "$cur" in ''|*[!0-9]*) cur=0;; esac
if [ "$cur" = "$APPLIED" ]; then
  # already compensated; just make sure the 5G AP is up
  pgrep -f "hostapd-openwifi-5g.conf" >/dev/null || \
    ( cd /root/openwifi && hostapd -B hostapd-openwifi-5g.conf ) 2>/dev/null || true
else
  set_lo "$NOM"
  pkill hostapd 2>/dev/null || true
  sleep 1
  ( cd /root/openwifi && hostapd -B hostapd-openwifi-5g.conf ) 2>/dev/null || true
  sleep 2
  set_lo "$APPLIED"
  sleep 1
  set_lo "$APPLIED"
fi
exit 0

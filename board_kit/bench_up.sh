#!/bin/sh
# bench_up.sh - one-shot experiment bring-up after board boot (idempotent).
#   [1] ap_up.sh   : FPGA bitstream + wireless drivers + hostapd (2.4G ch6)
#                    + 37kHz CFO on both LOs + NAT/DHCP
#   [2] csi_dma.ko : zero-copy CSI driver, auto_start=0 (demand-driven via
#                    csi_bench ioctl; does NOT interfere with TX beacons)
#   [3] iperf3 -s  : traffic server for the client uplink (192.168.13.1:5201)
#
# After this, on the X230:  iperf3 -c 192.168.13.1 -u -b 12M -l 1400 -t 1500
# then start the scan:      sh bench/wait_and_scan.sh
cd /root/openwifi || { echo "openwifi dir not found"; exit 1; }

echo "== [1/3] ap_up.sh (FPGA + drivers + AP + CFO + NAT) =="
./ap_up.sh || echo "WARNING: ap_up.sh rc=$?"

echo "== [2/3] csi_dma.ko auto_start=0 =="
rmmod side_ch 2>/dev/null
rmmod csi_dma 2>/dev/null
if insmod bench/csi_dma.ko auto_start=0; then
    ls /dev/csi_dma >/dev/null || { echo "ERROR: /dev/csi_dma missing"; exit 1; }
    echo "csi_dma loaded, /dev/csi_dma ready"
else
    echo "ERROR: csi_dma insmod failed (side_ch residue? reboot and retry)"
    exit 1
fi

echo "== [3/3] iperf3 server =="
pkill -x iperf3 2>/dev/null
sleep 1
nohup iperf3 -s >/tmp/iperf3_server.log 2>&1 &
sleep 1
pgrep -x iperf3 >/dev/null && echo "iperf3 server up (:5201)" || echo "WARNING: iperf3 server NOT running"

echo "== state =="
grep -E "^(sdr|csi_dma|xpu|openofdm_rx)" /proc/modules
echo "RX_LO=$(cat /sys/bus/iio/devices/iio:device1/out_altvoltage0_RX_LO_frequency)"
echo "TX_LO=$(cat /sys/bus/iio/devices/iio:device1/out_altvoltage1_TX_LO_frequency)"
iw dev sdr0 station dump | grep -E "^Station" || echo "(no station associated yet)"
echo "== bench_up done =="

#!/bin/sh
# wait_and_scan.sh - poll uplink traffic; when the fixed-rate iperf stream is
# detected (pilot fps > threshold), run the num_eq gradient scan automatically.
# Gives up after MAXWAIT minutes of silence.
#
# Usage (board, after bench_up.sh):  sh bench/wait_and_scan.sh
cd /root/openwifi/bench || exit 1
THRESH=800      # fps; 12 Mbps UDP uplink yields ~1000-1100
MAXWAIT=30      # minutes
START=$(date +%s)
while true; do
    ./csi_bench -d 3 -n 8 -j /tmp/wait_pilot.json >/dev/null 2>&1
    FPS=$(grep -o '"frame_rate_hz": [0-9.]*' /tmp/wait_pilot.json | cut -d' ' -f2)
    FPS=${FPS:-0}
    echo "$(date +%H:%M:%S) pilot ${FPS} fps (threshold ${THRESH})"
    if awk -v f="$FPS" -v t="$THRESH" 'BEGIN{exit !(f>t)}'; then
        echo "traffic detected -> starting num_eq scan"
        break
    fi
    if [ $(( $(date +%s) - START )) -gt $(( MAXWAIT * 60 )) ]; then
        echo "no traffic after ${MAXWAIT} min - giving up"
        exit 1
    fi
    sleep 12
done
./run_scan_num_eq.sh 5 60 scan_num_eq
echo "SCAN_SCRIPT_EXIT=$?"

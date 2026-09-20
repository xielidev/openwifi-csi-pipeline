#!/bin/bash
# run_repeat_baseline.sh - repeated baseline (side_ch) runs, for mean±std.
#
# Wraps run_baseline.py R times, producing baseline_r<N>.json (+ the paired
# baseline_r<N>_udp.json from the local UDP receiver). The mmap-side twin of
# this data is the num_eq=8 point of run_scan_num_eq.sh (mmap_ne8_r*.json) -
# keep both on the same board state, traffic and CFO for comparability.
#
# Usage: ./run_repeat_baseline.sh [rounds] [duration_s] [outdir] [extra run_baseline.py args...]
#   rounds    : repetitions (default 5)
#   duration_s: seconds per round (default 60)
#   outdir    : output dir (default ./repeats - same as run_repeats.sh)
#
# Preconditions:
#   - side_ch.ko loaded, csi_dma.ko NOT loaded (mutually exclusive DT node)
#   - side_ch_ctl next to this script; AP up; same fixed-rate uplink as the
#     mmap runs (e.g. client: iperf3 -c 192.168.13.1 -u -b 12M -l 1400 -t 360)
#
# After both sides: python3 summarize_runs.py --dir <outdir>
#
# SPDX-License-Identifier: AGPL-3.0-or-later
set -u
ROUNDS=${1:-5}
DUR=${2:-60}
OUTDIR=${3:-repeats}
if [ $# -ge 3 ]; then shift 3; else shift $#; fi
EXTRA="$*"

SCRIPT_DIR=$(dirname "$0")
if [ -x "$SCRIPT_DIR/run_baseline.py" ]; then
    RUN_BASELINE="$SCRIPT_DIR/run_baseline.py"
else
    RUN_BASELINE="$SCRIPT_DIR/../scripts/run_baseline.py"
fi
[ -f "$RUN_BASELINE" ] || { echo "ERROR: run_baseline.py not found next to this script"; exit 1; }
command -v python3 >/dev/null || { echo "ERROR: python3 required on the board"; exit 1; }
if [ -x "$SCRIPT_DIR/side_ch_ctl" ]; then
    export PATH="$SCRIPT_DIR:$PATH"
fi
command -v side_ch_ctl >/dev/null || { echo "ERROR: side_ch_ctl not found in PATH or next to this script"; exit 1; }

if grep -q '^csi_dma ' /proc/modules 2>/dev/null; then
    echo "ERROR: csi_dma.ko is loaded - rmmod csi_dma && insmod side_ch.ko first (mutually exclusive)" >&2
    exit 1
fi
grep -q '^side_ch ' /proc/modules 2>/dev/null || { echo "ERROR: side_ch.ko not loaded" >&2; exit 1; }

mkdir -p "$OUTDIR"
START=$(date +%s)
for R in $(seq 1 "$ROUNDS"); do
    OUT="$OUTDIR/baseline_r${R}.json"
    echo "[baseline] $(date +%H:%M:%S)  round=$R/$ROUNDS  (${DUR}s)  -> $OUT"
    python3 "$RUN_BASELINE" --duration "$DUR" --json "$OUT" $EXTRA \
        || echo "WARNING: round failed (round=$R)" >&2
    sleep 2
done
echo "[baseline] done in $(( $(date +%s) - START ))s"
echo "[baseline] next: python3 summarize_runs.py --dir $OUTDIR"

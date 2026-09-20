#!/bin/bash
# run_repeats.sh - repeated mmap runs of ONE config (tagged), for mean±std.
#
# This is the building block for the uplink-load gradient and for re-running
# any single point of the matrix. The num_eq scan has its own dedicated
# script (run_scan_num_eq.sh).
#
# Usage: ./run_repeats.sh <tag> <num_eq> [rounds] [duration_s] [outdir] [extra csi_bench args...]
#   tag       : short label, alphanumeric (e.g. load0, load5, load20)
#   num_eq    : equalizer blocks 0..8 (default 8)
#   rounds    : repetitions (default 5)
#   duration_s: seconds per round (default 60)
#   outdir    : output dir (default ./repeats)
#   extra args: passed through to csi_bench, e.g. -i <file> for the TSF dump
#
# Output: <outdir>/mmap_<tag>_r<N>.json  (aggregated by summarize_runs.py)
#
# Load-gradient example (uplink CSI traffic must come FROM the client):
#   on the board :  ./run_repeats.sh load0 8    (no iperf running)
#                   ./run_repeats.sh load5 8    (client: iperf3 -c 192.168.13.1 -u -b 5M  -l 1400 -t 360)
#                   ./run_repeats.sh load20 8   (client: iperf3 -c 192.168.13.1 -u -b 20M -l 1400 -t 360)
#
# SPDX-License-Identifier: AGPL-3.0-or-later
set -u
TAG=${1:?usage: run_repeats.sh <tag> <num_eq> [rounds] [duration_s] [outdir] [extra...]}
NUM_EQ=${2:-8}
ROUNDS=${3:-5}
DUR=${4:-60}
OUTDIR=${5:-repeats}
if [ $# -ge 5 ]; then shift 5; else shift $#; fi
EXTRA="$*"

case "$TAG" in *[!A-Za-z0-9]*|"") echo "ERROR: tag must be alphanumeric"; exit 1;; esac

SCRIPT_DIR=$(dirname "$0")
if [ -x "$SCRIPT_DIR/csi_bench" ]; then
    BENCH="$SCRIPT_DIR/csi_bench"
else
    BENCH="$SCRIPT_DIR/../user_space/csi_bench"
fi
[ -x "$BENCH" ] || { echo "ERROR: csi_bench not found next to this script"; exit 1; }

if grep -q '^side_ch ' /proc/modules 2>/dev/null; then
    echo "ERROR: side_ch.ko is loaded - rmmod side_ch first (mutually exclusive DT node)" >&2
    exit 1
fi
grep -q '^csi_dma ' /proc/modules 2>/dev/null || { echo "ERROR: csi_dma.ko not loaded" >&2; exit 1; }

mkdir -p "$OUTDIR"
START=$(date +%s)
for R in $(seq 1 "$ROUNDS"); do
    OUT="$OUTDIR/mmap_${TAG}_r${R}.json"
    echo "[repeats] $(date +%H:%M:%S)  tag=$TAG num_eq=$NUM_EQ  round=$R/$ROUNDS  (${DUR}s)  -> $OUT"
    "$BENCH" -d "$DUR" -n "$NUM_EQ" -j "$OUT" $EXTRA || echo "WARNING: round failed (tag=$TAG round=$R)" >&2
    sleep 2
done
echo "[repeats] done in $(( $(date +%s) - START ))s"
echo "[repeats] next: python3 summarize_runs.py --dir $OUTDIR"

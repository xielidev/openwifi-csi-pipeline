#!/bin/bash
# run_scan_num_eq.sh - num_eq gradient scan for the zero-copy mmap path.
#
# Runs csi_bench over num_eq in {0,2,4,8} x R rounds each, under a FIXED
# uplink traffic load, producing mmap_ne<ne>_r<round>.json per run.
# Purpose: show the capture path's overhead scales gracefully with the CSI
# frame size (num_eq drives frame bytes: 464/1296/2128/3792 B).
# Requires NO code changes: csi_bench already supports -n and the driver
# reprograms the FPGA NUM_EQ register via ioctl.
#
# Usage: ./run_scan_num_eq.sh [rounds] [duration_s] [outdir]
#   rounds    : repetitions per num_eq point (default 5)
#   duration_s: seconds per round (default 60)
#   outdir    : where the per-round JSONs go (default ./scan_num_eq)
#
# Environment overrides:
#   NUM_EQ_LIST : points to scan            (default "0 2 4 8")
#   MIN_FPS     : pilot traffic check, fps below -> abort (default 200)
#   FORCE=1     : warn instead of abort on the traffic check
#
# Preconditions (script aborts if violated):
#   - csi_dma.ko loaded (auto_start=0), side_ch.ko NOT loaded (mutually
#     exclusive - same DT node)
#   - AP up and a fixed-rate uplink running for the WHOLE scan, e.g. on the
#     client:  iperf3 -c 192.168.13.1 -u -b 12M -l 1400 -t <rounds*duration*4+60>
#     (a Redmi iperf3 client may stall after ~150 s; X230 is the safer source)
#
# After the scan, aggregate with (board or dev machine):
#   python3 summarize_runs.py --dir <outdir>
#
# SPDX-License-Identifier: AGPL-3.0-or-later
set -u
ROUNDS=${1:-5}
DUR=${2:-60}
OUTDIR=${3:-scan_num_eq}
NUM_EQ_LIST=${NUM_EQ_LIST:-"0 2 4 8"}
MIN_FPS=${MIN_FPS:-200}
FORCE=${FORCE:-0}

SCRIPT_DIR=$(dirname "$0")
if [ -x "$SCRIPT_DIR/csi_bench" ]; then
    BENCH="$SCRIPT_DIR/csi_bench"            # flat board_kit layout
else
    BENCH="$SCRIPT_DIR/../user_space/csi_bench"  # project tree layout
fi
[ -x "$BENCH" ] || { echo "ERROR: csi_bench not found next to this script"; exit 1; }

fail() { echo "ERROR: $*" >&2; exit 1; }

# ---- preconditions: driver state (mutual exclusion is load-bearing) --------
if grep -q '^side_ch ' /proc/modules 2>/dev/null; then
    fail "side_ch.ko is loaded - rmmod side_ch first (mutually exclusive DT node)"
fi
grep -q '^csi_dma ' /proc/modules 2>/dev/null || fail "csi_dma.ko not loaded (insmod csi_dma.ko auto_start=0)"
[ -e /dev/csi_dma ] || fail "/dev/csi_dma missing"
AUTO=$(cat /sys/module/csi_dma/parameters/auto_start 2>/dev/null || echo '?')
if [ "$AUTO" != "0" ]; then
    echo "WARNING: csi_dma auto_start=$AUTO (expected 0; auto_start=1 disturbs TX beacons)"
fi

mkdir -p "$OUTDIR"

# ---- pilot: is the fixed-rate uplink actually flowing? ----------------------
PILOT_JSON="$OUTDIR/pilot.json"
echo "[scan] pilot run: csi_bench -d 5 -n 8 (traffic sanity check)"
"$BENCH" -d 5 -n 8 -j "$PILOT_JSON" >/dev/null || fail "pilot run failed"
PILOT_FPS=$(python3 -c "import json;print(json.load(open('$PILOT_JSON'))['frame_rate_hz'])" 2>/dev/null || echo 0)
echo "[scan] pilot frame rate: ${PILOT_FPS} fps"
LOW=$(python3 -c "print(1 if float('$PILOT_FPS') < float('$MIN_FPS') else 0)")
if [ "$LOW" = "1" ]; then
    MSG="pilot ${PILOT_FPS} fps < MIN_FPS ${MIN_FPS} - uplink traffic looks idle/off.
       Start it on the client, e.g.: iperf3 -c 192.168.13.1 -u -b 12M -l 1400 -t $((ROUNDS * DUR * 4 + 60))
       (FORCE=1 to run anyway - numbers will reflect whatever traffic exists)"
    if [ "$FORCE" = "1" ]; then echo "WARNING: $MSG"; else fail "$MSG"; fi
fi

# ---- manifest ---------------------------------------------------------------
{
    echo "{"
    echo "  \"experiment\": \"num_eq gradient scan (mmap zero-copy path)\","
    echo "  \"date\": \"$(date -Iseconds)\","
    echo "  \"board\": \"$(uname -n)\","
    echo "  \"kernel\": \"$(uname -r)\","
    echo "  \"rounds\": $ROUNDS,"
    echo "  \"duration_s\": $DUR,"
    echo "  \"num_eq_list\": [$(echo "$NUM_EQ_LIST" | tr ' ' ',')],"
    echo "  \"pilot_fps\": $PILOT_FPS"
    echo "}"
} > "$OUTDIR/scan_meta.json"

# ---- the scan ----------------------------------------------------------------
TOTAL_START=$(date +%s)
for NE in $NUM_EQ_LIST; do
    for R in $(seq 1 "$ROUNDS"); do
        OUT="$OUTDIR/mmap_ne${NE}_r${R}.json"
        echo "[scan] $(date +%H:%M:%S)  num_eq=$NE  round=$R/$ROUNDS  (${DUR}s)  -> $OUT"
        "$BENCH" -d "$DUR" -n "$NE" -j "$OUT" || echo "WARNING: round failed (num_eq=$NE round=$R)" >&2
        sleep 2
    done
done
echo "[scan] done in $(( $(date +%s) - TOTAL_START ))s"

# ---- quick sanity: flag rounds far from their group median --------------------
python3 - "$OUTDIR" <<'EOF'
import glob, json, os, sys
d = sys.argv[1]
for ne in sorted({f.rsplit("_r", 1)[0] for f in os.listdir(d) if f.startswith("mmap_ne") and f.endswith(".json")}):
    rates = []
    for f in sorted(glob.glob(os.path.join(d, ne + "_r*.json"))):
        with open(f) as fh:
            rates.append((f, json.load(fh).get("frame_rate_hz", 0.0)))
    vals = sorted(r for _, r in rates)
    med = vals[len(vals) // 2] if vals else 0.0
    for f, r in rates:
        if med > 0 and r < 0.5 * med:
            print(f"[scan] SANITY: {os.path.basename(f)} fps={r:.1f} < 50% of group median {med:.1f} - traffic stall? consider re-running this round")
EOF
echo "[scan] next: python3 summarize_runs.py --dir $OUTDIR"

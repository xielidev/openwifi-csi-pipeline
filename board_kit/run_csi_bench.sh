#!/bin/bash
# run_csi_bench.sh - run the zero-copy mmap benchmark on the board.
#
# Usage: ./run_csi_bench.sh [duration_s] [num_eq] [json_out] [extra args...]
#   duration_s : seconds to capture (default 60)
#   num_eq     : equalizer blocks 0..8 (default 8)
#   json_out   : output JSON (default mmap.json)
#   extra args : passed through to csi_bench, e.g.:
#                  -r <pps>   expected source rate -> explicit loss%
#                  -i <file>  dump raw TSF deltas for the jitter CDF
#
# Requires: csi_dma.ko loaded, csi_bench built for the board (scripts/build.sh)
#
set -e
DUR=${1:-60}
NUM_EQ=${2:-8}
JSON=${3:-mmap.json}
shift 3 || true
SCRIPT_DIR=$(dirname "$0")
if [ -x "$SCRIPT_DIR/csi_bench" ]; then
    BENCH="$SCRIPT_DIR/csi_bench"        # flat board_kit layout
else
    BENCH="$SCRIPT_DIR/../user_space/csi_bench"  # project tree layout
fi

"$BENCH" -d "$DUR" -n "$NUM_EQ" -j "$JSON" "$@"
echo
echo "Result JSON: $JSON"

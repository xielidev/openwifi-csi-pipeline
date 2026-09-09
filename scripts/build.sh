#!/bin/bash
# build.sh - cross-compile csi_dma.ko + csi_bench for the board.
#
# Usage:
#   ./build.sh [XILINX_DIR] [OPENWIFI_DIR] [ARCH_BIT]
#
#   XILINX_DIR   : Xilinx install root containing Vitis/2022.2 (default /tools/Xilinx)
#   OPENWIFI_DIR : openwifi tree with adi-linux kernel source   (default /home/lab/Projects/RK-ZYNQ7020-F/openwifi)
#   ARCH_BIT     : 32 (Zynq-7020, default) or 64
#
set -e

XILINX_DIR=${1:-/tools/Xilinx}
OPENWIFI_DIR=${2:-/home/lab/Projects/RK-ZYNQ7020-F/openwifi}
ARCH_BIT=${3:-32}
ROOT=$(cd "$(dirname "$0")/.." && pwd)

ENV_FILE="$XILINX_DIR/Vitis/2022.2/settings64.sh"
if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found (check XILINX_DIR)" >&2
    exit 1
fi
source "$ENV_FILE"

if [ "$ARCH_BIT" = "64" ]; then
    KDIR="$OPENWIFI_DIR/adi-linux-64/"
    ARCH=arm64
    CROSS_COMPILE=aarch64-linux-gnu-
else
    KDIR="$OPENWIFI_DIR/adi-linux/"
    ARCH=arm
    CROSS_COMPILE=arm-linux-gnueabihf-
fi

if [ ! -d "$KDIR" ]; then
    echo "ERROR: kernel source $KDIR not found (check OPENWIFI_DIR)" >&2
    exit 1
fi

echo "== building driver/csi_dma.ko =="
make -C "$ROOT/driver" KDIR="$KDIR" ARCH=$ARCH CROSS_COMPILE=$CROSS_COMPILE

echo "== building user_space/csi_bench =="
make -C "$ROOT/user_space" CC="${CROSS_COMPILE}gcc"

echo
echo "Done. Artifacts:"
echo "  $ROOT/driver/csi_dma.ko"
echo "  $ROOT/user_space/csi_bench (ARM ELF)"
echo
echo "Copy to the board, e.g.:"
echo "  scp $ROOT/driver/csi_dma.ko root@<board>:/root/"
echo "  scp $ROOT/user_space/csi_bench root@<board>:/root/"

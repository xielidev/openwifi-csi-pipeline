#!/bin/sh
# set_txlo.sh <offset_khz> - set TX_LO = ch6 nominal (2437 MHz) + offset, in Hz.
# RX_LO is intentionally NOT touched (changing it can drop associated clients).
# CFO bisection helper: the board crystal's actual error drifts between cold
# boots (observed +32kHz on 2026-09-11, +37kHz on 2026-09-12); X230 (Intel)
# beacon detection is the sharp observable.
# Usage (board):  ./set_txlo.sh 37000
[ -n "$1" ] || { echo "usage: set_txlo.sh <offset_khz>"; exit 1; }
echo $((2437000000 + $1)) > /sys/bus/iio/devices/iio:device1/out_altvoltage1_TX_LO_frequency
echo "RX_LO=$(cat /sys/bus/iio/devices/iio:device1/out_altvoltage0_RX_LO_frequency)"
echo "TX_LO=$(cat /sys/bus/iio/devices/iio:device1/out_altvoltage1_TX_LO_frequency)"

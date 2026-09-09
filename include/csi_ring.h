/*
 * csi_ring.h - shared layout between csi_dma kernel driver and userspace tools
 *
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * Ring buffer layout (single dma_alloc_coherent buffer, mmap'd to userspace):
 *
 *   +------------------------------+ 0x0000
 *   | struct csi_ring_meta (1 page)|
 *   +------------------------------+ 0x1000
 *   | slot 0 (slot_size bytes)     |
 *   +------------------------------+
 *   | slot 1 ...                   |
 *   +------------------------------+
 *   | slot N-1                     |
 *   +------------------------------+
 *
 * Each slot holds exactly one CSI frame produced by one AXI DMA transfer.
 * The driver (producer) writes frames into slot[producer_idx % N] and then
 * advances producer_idx. Userspace (consumer) reads new slots by watching
 * producer_idx in the metadata header, which is visible immediately because
 * the whole buffer is DMA-coherent (uncached on Zynq).
 */

#ifndef CSI_RING_H
#define CSI_RING_H

#include <linux/types.h>

#define CSI_DEV_NAME        "csi_dma"
#define CSI_RING_MAGIC      0x43534944U   /* "CSID" */
#define CSI_RING_VERSION    1

/* Frame layout constants, aligned with openwifi side_ch.v / side_ch.h */
#define CSI_SYMBOL_BYTES    8
#define CSI_HEADER_LEN      2   /* symbol 0: TSF timestamp, symbol 1: phase offset */
#define CSI_CSI_LEN         56
#define CSI_EQUALIZER_LEN   (56 - 4)   /* 48 usable + 4 padded for non-HT */

#define CSI_FRAME_SYMBOLS(_num_eq) \
	(CSI_HEADER_LEN + CSI_CSI_LEN + (_num_eq) * CSI_EQUALIZER_LEN)
#define CSI_FRAME_BYTES(_num_eq) \
	(CSI_FRAME_SYMBOLS(_num_eq) * CSI_SYMBOL_BYTES)

#define CSI_DEFAULT_NUM_EQ   8
#define CSI_DEFAULT_SLOT_SIZE 4096
#define CSI_DEFAULT_NUM_SLOTS 16
#define CSI_RING_HEADER_SIZE  4096

/* Metadata header, first page of the mmap'd buffer */
struct csi_ring_meta {
	__u32 magic;
	__u32 version;
	__u32 num_slots;
	__u32 slot_size;
	__u32 frame_bytes;      /* bytes per CSI frame for current num_eq */
	__u32 num_eq;           /* current number of equalizer blocks */
	__u32 producer_idx;     /* driver advances after each DMA transfer */
	__u32 frame_seq;        /* total frames captured since start */
	__u32 dma_err_count;    /* DMA errors / timeouts */
	__u32 last_transfer_us; /* duration of last DMA transfer (debug) */
	__u32 reserved[5];
};

/* ioctl commands */
#define CSI_DMA_IOCTL_MAGIC   0xCD
#define CSI_DMA_IOCTL_START        _IO(CSI_DMA_IOCTL_MAGIC, 1)
#define CSI_DMA_IOCTL_STOP         _IO(CSI_DMA_IOCTL_MAGIC, 2)
#define CSI_DMA_IOCTL_SET_NUM_EQ   _IOW(CSI_DMA_IOCTL_MAGIC, 3, __u32)
#define CSI_DMA_IOCTL_GET_STATS    _IOR(CSI_DMA_IOCTL_MAGIC, 4, struct csi_ring_meta)

/* Frame field offsets (in symbols), for userspace parsing */
#define CSI_TSF_OFFSET_SYM   0   /* 64-bit 802.11 TSF timestamp (us) */
#define CSI_FREQ_OFFSET_SYM  1   /* 32-bit phase offset + padding */

#endif /* CSI_RING_H */

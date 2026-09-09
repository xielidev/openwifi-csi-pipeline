#!/usr/bin/env python3
"""
draw_paper_figs.py - generate paper Fig.1 (architecture: stock vs zero-copy)
and Fig.2 (coherent ring buffer data-flow) as vector diagrams.

These are the two diagrams that CAN be finalised today (no experiments needed).
Fig.3 (benchmark sweep) is filled in from the board later.

Dependencies: matplotlib (with patches/annotations for boxes & arrows).
Run from repo root:
    python3 docs/draw_paper_figs.py
Outputs:
    docs/figures/fig1_architecture.png / .pdf
    docs/figures/fig2_ring.png / .pdf

SPDX-License-Identifier: AGPL-3.0-or-later
"""

import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

OUT = os.path.join(os.path.dirname(__file__), "figures")
os.makedirs(OUT, exist_ok=True)


def box(ax, x, y, w, h, text, fc="#eef3fb", ec="#2a7de1", fs=9, tc="black"):
    b = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02",
                       facecolor=fc, edgecolor=ec, linewidth=1.2)
    ax.add_patch(b)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            fontsize=fs, color=tc, wrap=True)


def arrow(ax, x1, y1, x2, y2, text=None, color="#333333", fs=7.5,
          style="-|>", ts="above"):
    a = FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style,
                        mutation_scale=14, linewidth=1.3, color=color)
    ax.add_patch(a)
    if text:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        dy = 0.045 if ts == "above" else -0.045
        ax.text(mx, my + dy, text, ha="center", va="center",
                fontsize=fs, color=color)


# ---------------------------------------------------------------- Fig.1
fig, ax = plt.subplots(figsize=(8.6, 3.6))
ax.set_xlim(0, 10); ax.set_ylim(0, 2.4); ax.axis("off")

# Panel (a) stock
ax.text(0.1, 2.15, "(a) Stock openwifi CSI path", fontsize=10, fontweight="bold")
box(ax, 0.35, 1.45, 1.2, 0.5, "side_ch.v\n(FPGA)", fc="#f2d7ee", ec="#a54a8b")
box(ax, 2.15, 1.45, 1.3, 0.5, "AXI DMA\n(map/unmap\nper frame)", fc="#eef3fb")
box(ax, 4.05, 1.45, 1.25, 0.5, "kernel\nkmalloc buf", fc="#eef3fb")
box(ax, 5.9, 1.45, 1.3, 0.5, "netlink\ncopy", fc="#fdf3d8", ec="#c9a227")
box(ax, 7.75, 1.45, 1.3, 0.5, "side_ch_ctl\n(userspace)", fc="#eaf7ea", ec="#3a943a")
arrow(ax, 1.55, 1.7, 2.15, 1.7, "DMA")
arrow(ax, 3.45, 1.7, 4.05, 1.7)
arrow(ax, 5.3, 1.7, 5.9, 1.7, "copy #1")
arrow(ax, 7.2, 1.7, 7.75, 1.7, "copy #2 / upcall")
# poll tick annotation
ax.text(6.5, 1.15, "poll every ~100 ms  ·  CPU copy ×2  ·  per-frame map/unmap",
        ha="center", fontsize=8, color="#a54a8b")

# Panel (b) proposed
ax.text(0.1, 0.85, "(b) Proposed zero-copy CSI path", fontsize=10, fontweight="bold")
box(ax, 0.35, 0.2, 1.2, 0.5, "side_ch.v\n(FPGA)", fc="#f2d7ee", ec="#a54a8b")
box(ax, 2.15, 0.2, 1.55, 0.5, "continuous\nAXI DMA  (once)", fc="#eef3fb")
box(ax, 4.3, 0.2, 1.6, 0.5, "coherent ring\n(mmap'd)", fc="#fdf3d8", ec="#c9a227")
box(ax, 6.55, 0.2, 1.9, 0.5, "application\n(poll → read)", fc="#eaf7ea", ec="#3a943a")
arrow(ax, 1.55, 0.45, 2.15, 0.45, "DMA")
arrow(ax, 3.7, 0.45, 4.3, 0.45, "zero-copy")
arrow(ax, 5.9, 0.45, 6.55, 0.45, "mmap\n(mapped once)", ts="above")
ax.text(7.5, 0.02, "1× DMA · 0× CPU copy · interrupt-driven",
        ha="center", fontsize=8, color="#3a943a")

fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig1_architecture.png"), dpi=200)
fig.savefig(os.path.join(OUT, "fig1_architecture.pdf"))
plt.close(fig)

# ---------------------------------------------------------------- Fig.2
fig, ax = plt.subplots(figsize=(8.2, 3.4))
ax.set_xlim(0, 10); ax.set_ylim(0, 2.4); ax.axis("off")

box(ax, 0.3, 1.7, 1.6, 0.5, "header page:\nproducer_idx, frame_seq,\nslot_size, num_eq", fc="#fdf3d8", ec="#c9a227")
box(ax, 2.4, 1.7, 1.15, 0.5, "slot 0", fc="#eaf7ea", ec="#3a943a")
box(ax, 3.7, 1.7, 1.15, 0.5, "slot 1", fc="#eaf7ea", ec="#3a943a")
box(ax, 5.0, 1.7, 1.15, 0.5, "slot i\n(producer_idx % N)", fc="#f2d7ee", ec="#a54a8b")
box(ax, 6.35, 1.7, 1.15, 0.5, "…", fc="#eaf7ea", ec="#3a943a")
box(ax, 7.6, 1.7, 1.15, 0.5, "slot N-1", fc="#eaf7ea", ec="#3a943a")

# producer arrow
arrow(ax, 1.9, 2.17, 2.4, 2.17, None, color="#2a7de1")
ax.text(2.15, 2.25, "DMA (producer): write frame → slot[i], bump producer_idx",
        ha="center", fontsize=8, color="#2a7de1")
# consumer read
arrow(ax, 5.55, 1.65, 5.55, 1.1, None, color="#3a943a", style="-")
arrow(ax, 5.55, 1.05, 2.2, 1.05, None, color="#3a943a", style="-")
ax.text(3.9, 0.92, "application: poll() → read slot[i] directly (no kernel copy)",
        ha="center", fontsize=8, color="#3a943a")
# header read arrow
arrow(ax, 2.0, 1.6, 2.0, 1.1, None, color="#c9a227", style="-")
arrow(ax, 2.0, 1.05, 2.2, 1.05, None, color="#c9a227", style="-")
ax.text(2.0, 0.62, "read producer_idx (coherent, visible directly)",
        ha="center", fontsize=8, color="#c9a227")

fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig2_ring.png"), dpi=200)
fig.savefig(os.path.join(OUT, "fig2_ring.pdf"))
plt.close(fig)

print("wrote fig1_architecture.{png,pdf} and fig2_ring.{png,pdf} to", OUT)
/*
 * csi_bench.c - userspace benchmark for the zero-copy CSI pipeline
 *
 * Opens /dev/csi_dma, mmaps the coherent ring buffer, reads CSI frames
 * directly with no CPU copy, and measures the three headline numbers:
 *   1. frame rate (frames/s)
 *   2. process CPU%
 *   3. timestamp jitter (std of inter-frame 802.11 TSF deltas, us)
 *
 * Optionally forwards frames to a remote UDP consumer (same role as the
 * baseline side_ch_ctl -> csi_udp_recv.py path) or dumps raw frames.
 *
 * Usage:
 *   csi_bench [-d <seconds>] [-n <num_eq>] [-j <json_out>]
 *             [-u <udp_ip>] [-p <udp_port>] [-f <raw_dump_file>]
 *
 * SPDX-License-Identifier: AGPL-3.0-or-later
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <poll.h>
#include <getopt.h>
#include <math.h>
#include <time.h>
#include <sys/mman.h>
#include <sys/ioctl.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>

#include <linux/types.h>
#include <linux/ioctl.h>

#include "csi_ring.h"

#define DEV_PATH "/dev/csi_dma"
#define HEADER_SIZE 4096

struct intervals {
	uint64_t *v;
	size_t n;
	size_t cap;
};

static void intervals_add(struct intervals *iv, uint64_t val)
{
	if (iv->n == iv->cap) {
		iv->cap = iv->cap ? iv->cap * 2 : 4096;
		iv->v = realloc(iv->v, iv->cap * sizeof(*iv->v));
		if (!iv->v) {
			fprintf(stderr, "realloc failed\n");
			exit(1);
		}
	}
	iv->v[iv->n++] = val;
}

static int cmp_u64(const void *a, const void *b)
{
	uint64_t x = *(const uint64_t *)a, y = *(const uint64_t *)b;
	return (x > y) - (x < y);
}

static uint64_t pct(const struct intervals *iv, double p)
{
	size_t idx = (size_t)((double)(iv->n - 1) * p);
	return iv->v[idx];
}

static double now_s(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

static void usage(const char *prog)
{
	fprintf(stderr,
		"Usage: %s [-d <sec>] [-n <num_eq>] [-j <json_out>]\n"
		"            [-u <udp_ip>] [-p <udp_port>] [-f <raw_dump>]\n"
		"            [-r <expected_pps>] [-i <intervals_out>]\n"
		"  -d  capture duration in seconds (default 10)\n"
		"  -n  number of equalizer blocks appended to CSI, 0..8 (default 8)\n"
		"  -j  write machine-readable JSON summary to <json_out>\n"
		"  -u  forward frames via UDP to <udp_ip> (default: no forwarding)\n"
		"  -p  UDP port (default 4000)\n"
		"  -f  dump raw frames to <raw_dump> for offline verification\n"
		"  -r  expected source rate in frames/s -> enables explicit loss%%\n"
		"  -i  dump raw inter-frame TSF deltas (us) to <intervals_out> (for CDF)\n",
		prog);
}

int main(int argc, char **argv)
{
	int fd, c, ret;
	int duration = 10, num_eq = CSI_DEFAULT_NUM_EQ, udp_port = 4000;
	const char *json_out = NULL, *udp_ip = NULL, *raw_path = NULL;
	const char *intervals_out = NULL;
	double expected_rate = 0.0;
	FILE *raw_fp = NULL, *iv_fp = NULL;
	int udp_fd = -1;
	struct sockaddr_in udp_dst;
	struct csi_ring_meta meta;
	void *base = MAP_FAILED;
	size_t buf_size;
	struct csi_ring_meta *m;
	uint32_t last;
	uint64_t total = 0, prev_tsf = 0;
	struct intervals iv = {0};
	struct rusage ru0, ru1;
	double t_start, t_end, elapsed, cpu_percent;
	FILE *jfp = NULL;

	while ((c = getopt(argc, argv, "d:n:j:u:p:f:r:i:h")) != -1) {
		switch (c) {
		case 'd': duration = atoi(optarg); break;
		case 'n': num_eq = atoi(optarg); break;
		case 'j': json_out = optarg; break;
		case 'u': udp_ip = optarg; break;
		case 'p': udp_port = atoi(optarg); break;
		case 'f': raw_path = optarg; break;
		case 'r': expected_rate = atof(optarg); break;
		case 'i': intervals_out = optarg; break;
		default:  usage(argv[0]); return 1;
		}
	}
	if (num_eq < 0 || num_eq > 8) {
		fprintf(stderr, "num_eq must be 0..8\n");
		return 1;
	}

	fd = open(DEV_PATH, O_RDWR);
	if (fd < 0) {
		fprintf(stderr, "open %s: %s\n", DEV_PATH, strerror(errno));
		return 1;
	}

	if (ioctl(fd, CSI_DMA_IOCTL_SET_NUM_EQ, &num_eq) < 0) {
		perror("ioctl SET_NUM_EQ");
		goto out;
	}
	if (ioctl(fd, CSI_DMA_IOCTL_GET_STATS, &meta) < 0) {
		perror("ioctl GET_STATS");
		goto out;
	}
	if (meta.magic != CSI_RING_MAGIC) {
		fprintf(stderr, "bad ring magic 0x%x\n", meta.magic);
		goto out;
	}

	buf_size = HEADER_SIZE + (size_t)meta.num_slots * meta.slot_size;
	base = mmap(NULL, buf_size, PROT_READ, MAP_SHARED, fd, 0);
	if (base == MAP_FAILED) {
		perror("mmap");
		goto out;
	}
	m = (struct csi_ring_meta *)base;
	last = m->producer_idx;

	if (udp_ip) {
		udp_fd = socket(AF_INET, SOCK_DGRAM, 0);
		if (udp_fd < 0) {
			perror("socket");
			goto out;
		}
		memset(&udp_dst, 0, sizeof(udp_dst));
		udp_dst.sin_family = AF_INET;
		udp_dst.sin_port = htons(udp_port);
		if (inet_pton(AF_INET, udp_ip, &udp_dst.sin_addr) != 1) {
			fprintf(stderr, "invalid UDP ip %s\n", udp_ip);
			goto out;
		}
	}
	if (raw_path) {
		raw_fp = fopen(raw_path, "wb");
		if (!raw_fp) {
			perror("fopen raw");
			goto out;
		}
	}
	if (intervals_out) {
		iv_fp = fopen(intervals_out, "w");
		if (!iv_fp) {
			perror("fopen intervals");
			goto out;
		}
	}
	if (json_out) {
		jfp = fopen(json_out, "w");
		if (!jfp) {
			perror("fopen json");
			goto out;
		}
	}

	if (ioctl(fd, CSI_DMA_IOCTL_START) < 0) {
		perror("ioctl START");
		goto out;
	}

	getrusage(RUSAGE_SELF, &ru0);
	t_start = now_s();

	/* capture loop */
	while (1) {
		struct pollfd pfd = { .fd = fd, .events = POLLIN };
		int pr;
		uint32_t prod, new_frames, i;

		pr = poll(&pfd, 1, 200);
		if (pr < 0) {
			if (errno == EINTR)
				continue;
			perror("poll");
			break;
		}
		if (pr == 0) /* timeout: still consuming; check elapsed below */
			;
		if (pr > 0 && (pfd.revents & (POLLIN | POLLERR))) {
			prod = m->producer_idx;
			if (prod != last) {
				new_frames = prod - last;
				for (i = 0; i < new_frames; i++) {
					uint32_t idx = (last + i) % m->num_slots;
					const uint8_t *slot =
						(const uint8_t *)base + HEADER_SIZE +
						(size_t)idx * m->slot_size;
					uint64_t tsf;
					memcpy(&tsf, slot, sizeof(tsf));
					if (total > 0) {
						uint64_t d = tsf - prev_tsf;
						intervals_add(&iv, d);
						if (iv_fp)
							fprintf(iv_fp, "%llu\n",
								(unsigned long long)d);
					}
					prev_tsf = tsf;
					total++;
					if (udp_fd >= 0)
						sendto(udp_fd, slot, m->frame_bytes, 0,
						       (struct sockaddr *)&udp_dst,
						       sizeof(udp_dst));
					if (raw_fp)
						fwrite(slot, 1, m->frame_bytes, raw_fp);
				}
				last = prod;
			}
		}
		t_end = now_s();
		if (t_end - t_start >= (double)duration)
			break;
	}

	getrusage(RUSAGE_SELF, &ru1);
	t_end = now_s();
	elapsed = t_end - t_start;

	ioctl(fd, CSI_DMA_IOCTL_STOP);

	if (ioctl(fd, CSI_DMA_IOCTL_GET_STATS, &meta) < 0)
		perror("ioctl GET_STATS(final)");

	/* ---- stats ---- */
	cpu_percent = ((double)((ru1.ru_utime.tv_sec + ru1.ru_stime.tv_sec) -
				(ru0.ru_utime.tv_sec + ru0.ru_stime.tv_sec)) +
		       (double)((ru1.ru_utime.tv_usec + ru1.ru_stime.tv_usec) -
				(ru0.ru_utime.tv_usec + ru0.ru_stime.tv_usec)) / 1e6)
		      / elapsed * 100.0;

	double frame_rate = (double)total / elapsed;
	double jit_mean = 0, jit_std = 0, jit_p50 = 0, jit_p95 = 0;

	if (iv.n > 0) {
		qsort(iv.v, iv.n, sizeof(*iv.v), cmp_u64);
		for (size_t i = 0; i < iv.n; i++)
			jit_mean += (double)iv.v[i];
		jit_mean /= (double)iv.n;
		for (size_t i = 0; i < iv.n; i++) {
			double d = (double)iv.v[i] - jit_mean;
			jit_std += d * d;
		}
		jit_std = sqrt(jit_std / (double)iv.n);
		jit_p50 = (double)pct(&iv, 0.50);
		jit_p95 = (double)pct(&iv, 0.95);
	}

	/* ---- loss estimates ---- */
	double loss_percent = 0.0;
	if (expected_rate > 0.0 && elapsed > 0.0) {
		double expected = expected_rate * elapsed;
		if (expected > 0.0)
			loss_percent = (1.0 - (double)total / expected) * 100.0;
	}
	double tsf_loss_est_percent = 0.0;
	if (iv.n > 0 && jit_p50 > 0.0) {
		uint64_t lost = 0;
		for (size_t i = 0; i < iv.n; i++) {
			if (iv.v[i] > (uint64_t)(1.5 * jit_p50)) {
				double k = (double)iv.v[i] / jit_p50;
				lost += (uint64_t)llround(k) - 1;
			}
		}
		tsf_loss_est_percent = (double)lost / ((double)total + (double)lost) * 100.0;
	}

	/* ---- report ---- */
	printf("== csi_bench (mmap zero-copy) ==\n");
	printf("  duration       : %.2f s\n", elapsed);
	printf("  num_eq         : %u\n", meta.num_eq);
	printf("  total frames   : %llu\n", (unsigned long long)total);
	printf("  frame rate     : %.1f frames/s\n", frame_rate);
	printf("  loss%%          : explicit %.2f%%  (TSF-est %.2f%%)\n",
	       loss_percent, tsf_loss_est_percent);
	printf("  CPU%%           : %.2f%%\n", cpu_percent);
	printf("  TSF interval   : mean %.1f us  std %.1f us  p50 %.1f us  p95 %.1f us\n",
	       jit_mean, jit_std, jit_p50, jit_p95);
	printf("  dma errors     : %u\n", meta.dma_err_count);

	if (jfp) {
		fprintf(jfp,
			"{\n"
			"  \"mode\": \"mmap\",\n"
			"  \"duration_s\": %.2f,\n"
			"  \"num_eq\": %u,\n"
			"  \"total_frames\": %llu,\n"
			"  \"frame_rate_hz\": %.1f,\n"
			"  \"loss_percent\": %.2f,\n"
			"  \"tsf_loss_est_percent\": %.2f,\n"
			"  \"cpu_percent\": %.2f,\n"
			"  \"dma_err_count\": %u,\n"
			"  \"jitter_us\": {\"mean\": %.1f, \"std\": %.1f, \"p50\": %.1f, \"p95\": %.1f}\n"
			"}\n",
			elapsed, meta.num_eq, (unsigned long long)total,
			frame_rate, loss_percent, tsf_loss_est_percent,
			cpu_percent, meta.dma_err_count,
			jit_mean, jit_std, jit_p50, jit_p95);
		fclose(jfp);
	}

	ret = 0;
out:
	if (raw_fp)
		fclose(raw_fp);
	if (iv_fp)
		fclose(iv_fp);
	if (base != MAP_FAILED)
		munmap(base, buf_size);
	if (udp_fd >= 0)
		close(udp_fd);
	close(fd);
	return ret;
}

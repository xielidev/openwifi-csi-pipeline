/*
 * csi_dma.c - zero-copy CSI streaming driver for openwifi
 *
 * V0 design:
 *   - Reuses the "sdr,side_ch" DT node of the openwifi side channel, so this
 *     module is mutually exclusive with side_ch.ko (the device is claimed by
 *     whichever module probes first).
 *   - A kthread keeps an AXI DMA S2MM transfer armed at all times. Each write
 *     of the NUM_DMA_SYMBOL register makes the PL stream exactly one CSI frame
 *     (frame_symbols symbols) directly into a pre-allocated coherent slot.
 *   - The ring buffer is dma_alloc_coherent (uncached, no SMMU on Zynq-7020),
 *     so DMA writes land where userspace reads them with NO CPU copy and NO
 *     per-transfer map/unmap. Userspace gets the buffer via mmap and is
 *     notified of new frames via poll.
 *
 * Compared to the baseline side_ch driver (netlink pull at 100ms + one-shot
 * dma_map_single + nlmsg_unicast copy), this removes the CPU copies, the
 * per-transfer DMA mapping and the polling latency, which is exactly the
 * mmap vs baseline comparison measured by user_space/csi_bench.c.
 *
 * Author: (your name) <(your email)>
 * SPDX-License-Identifier: AGPL-3.0-or-later
 */

#include <linux/init.h>
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/platform_device.h>
#include <linux/of.h>
#include <linux/io.h>
#include <linux/dmaengine.h>
#include <linux/dma-mapping.h>
#include <linux/miscdevice.h>
#include <linux/mm.h>
#include <linux/poll.h>
#include <linux/mutex.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/delay.h>
#include <linux/kthread.h>
#include <linux/completion.h>

#include "csi_ring.h"

#define CSI_DRV_NAME "csi_dma"

/* Register offsets, aligned with side_ch.h */
#define SIDE_CH_REG_MULTI_RST_ADDR            (0 * 4)
#define SIDE_CH_REG_CONFIG_ADDR               (1 * 4)
#define SIDE_CH_REG_NUM_DMA_SYMBOL_ADDR       (2 * 4)
#define SIDE_CH_REG_IQ_CAPTURE_ADDR           (3 * 4)
#define SIDE_CH_REG_NUM_EQ_ADDR               (4 * 4)
#define SIDE_CH_REG_IQ_TRIGGER_ADDR           (8 * 4)
#define SIDE_CH_REG_PRE_TRIGGER_LEN_ADDR      (11 * 4)
#define SIDE_CH_REG_IQ_LEN_ADDR               (12 * 4)

#define CSI_DMA_TIMEOUT_MS  2000  /* defensive only; stream is event-driven */

static int num_eq_init = CSI_DEFAULT_NUM_EQ;
module_param(num_eq_init, int, 0);
MODULE_PARM_DESC(num_eq_init, "number of equalizer blocks appended to CSI (0..8)");

static int slot_size_init = CSI_DEFAULT_SLOT_SIZE;
module_param(slot_size_init, int, 0);
MODULE_PARM_DESC(slot_size_init, "bytes per ring slot (must hold one frame)");

static int num_slots_init = CSI_DEFAULT_NUM_SLOTS;
module_param(num_slots_init, int, 0);
MODULE_PARM_DESC(num_slots_init, "number of ring slots");

static int auto_start = 1;
module_param(auto_start, int, 0);
MODULE_PARM_DESC(auto_start, "start streaming immediately on probe (default 1)");

struct csi_dma_dev {
	struct device *dev;
	void __iomem *base;
	struct dma_chan *chan;

	struct csi_ring_meta *meta;   /* header page of the coherent buffer */
	u8 *ring;                      /* slots, == (u8 *)meta + header size */
	dma_addr_t ring_dma;
	size_t dma_buf_size;

	unsigned int num_slots;
	unsigned int slot_size;
	unsigned int frame_bytes;
	unsigned int frame_symbols;
	unsigned int num_eq;

	struct completion dma_cmp;
	struct task_struct *kthread;
	wait_queue_head_t poll_wq;
	struct mutex lock;
	bool running;

	u32 producer_idx;
	u32 frame_seq;
	u32 dma_err_count;

	struct miscdevice misc;
};

static inline u32 csi_reg_read(struct csi_dma_dev *dev, u32 reg)
{
	return ioread32(dev->base + reg);
}

static inline void csi_reg_write(struct csi_dma_dev *dev, u32 reg, u32 val)
{
	iowrite32(val, dev->base + reg);
}

static void csi_dma_callback(void *param)
{
	struct completion *cmp = param;

	complete(cmp);
}

/* One DMA transfer: pull exactly one CSI frame into the next ring slot. */
static int csi_dma_transfer(struct csi_dma_dev *dev)
{
	struct dma_device *dma_dev = dev->chan->device;
	struct dma_async_tx_descriptor *desc;
	struct scatterlist sg;
	dma_cookie_t cookie;
	unsigned int slot_idx;
	u8 *slot;
	dma_addr_t slot_dma;
	enum dma_ctrl_flags flags = DMA_CTRL_ACK | DMA_PREP_INTERRUPT;
	unsigned long tmo = msecs_to_jiffies(CSI_DMA_TIMEOUT_MS);
	ktime_t t0, t1;
	unsigned long ret;

	slot_idx = dev->producer_idx % dev->num_slots;
	slot = dev->ring + (size_t)slot_idx * dev->slot_size;
	slot_dma = dev->ring_dma + CSI_RING_HEADER_SIZE +
		   (size_t)slot_idx * dev->slot_size;

	sg_init_table(&sg, 1);
	sg_dma_address(&sg) = slot_dma;
	sg_dma_len(&sg) = dev->frame_bytes;

	desc = dma_dev->device_prep_slave_sg(dev->chan, &sg, 1,
					    DMA_DEV_TO_MEM, flags, NULL);
	if (!desc) {
		dev->dma_err_count++;
		dev_err(dev->dev, "%s: device_prep_slave_sg failed\n", __func__);
		return -EIO;
	}

	reinit_completion(&dev->dma_cmp);
	desc->callback = csi_dma_callback;
	desc->callback_param = &dev->dma_cmp;

	cookie = desc->tx_submit(desc);
	if (dma_submit_error(cookie)) {
		dev->dma_err_count++;
		dev_err(dev->dev, "%s: dma_submit_error %d\n", __func__, cookie);
		return -EIO;
	}

	dma_async_issue_pending(dev->chan);

	t0 = ktime_get();

	/* Trigger the PL to stream exactly one frame into the S2MM channel. */
	csi_reg_write(dev, SIDE_CH_REG_NUM_DMA_SYMBOL_ADDR,
		      dev->frame_symbols);

	ret = wait_for_completion_timeout(&dev->dma_cmp, tmo);
	if (ret == 0) {
		dev->dma_err_count++;
		dev_err(dev->dev, "%s: DMA timeout (no data or stuck)\n",
			__func__);
		dmaengine_terminate_all(dev->chan);
		return -ETIMEDOUT;
	}

	t1 = ktime_get();
	dev->meta->last_transfer_us = (u32)ktime_us_delta(t1, t0);

	dev->producer_idx++;
	dev->frame_seq++;
	dev->meta->producer_idx = dev->producer_idx;
	dev->meta->frame_seq = dev->frame_seq;
	dev->meta->dma_err_count = dev->dma_err_count;

	wake_up_all(&dev->poll_wq);
	return 0;
}

static int csi_dma_thread(void *data)
{
	struct csi_dma_dev *dev = data;

	while (!kthread_should_stop()) {
		if (!dev->running) {
			set_current_state(TASK_INTERRUPTIBLE);
			schedule_timeout(msecs_to_jiffies(10));
			continue;
		}
		if (csi_dma_transfer(dev) == -ETIMEDOUT)
			msleep(50);
	}
	return 0;
}

/* ------------------------------ char device ------------------------------ */

struct csi_dma_file {
	struct csi_dma_dev *dev;
	u32 last_producer_idx;
};

static int csi_dma_open(struct inode *inode, struct file *filp)
{
	struct csi_dma_dev *dev = container_of(filp->private_data,
					       struct csi_dma_dev, misc);
	struct csi_dma_file *pf;

	pf = kzalloc(sizeof(*pf), GFP_KERNEL);
	if (!pf)
		return -ENOMEM;
	pf->dev = dev;
	pf->last_producer_idx = dev->producer_idx;
	filp->private_data = pf;
	return 0;
}

static int csi_dma_release(struct inode *inode, struct file *filp)
{
	kfree(filp->private_data);
	return 0;
}

static int csi_dma_mmap(struct file *filp, struct vm_area_struct *vma)
{
	struct csi_dma_file *pf = filp->private_data;
	struct csi_dma_dev *dev = pf->dev;
	unsigned long size = vma->vm_end - vma->vm_start;

	if (size > dev->dma_buf_size)
		return -EINVAL;

	return dma_mmap_coherent(dev->dev, vma, dev->meta,
				 dev->ring_dma, dev->dma_buf_size);
}

static __poll_t csi_dma_poll(struct file *filp, poll_table *wait)
{
	struct csi_dma_file *pf = filp->private_data;
	struct csi_dma_dev *dev = pf->dev;
	__poll_t mask = 0;

	poll_wait(filp, &dev->poll_wq, wait);

	if (dev->producer_idx != pf->last_producer_idx) {
		pf->last_producer_idx = dev->producer_idx;
		mask |= EPOLLIN;
	}
	return mask;
}

static long csi_dma_ioctl(struct file *filp, unsigned int cmd, unsigned long arg)
{
	struct csi_dma_file *pf = filp->private_data;
	struct csi_dma_dev *dev = pf->dev;
	unsigned int neq;

	switch (cmd) {
	case CSI_DMA_IOCTL_START:
		mutex_lock(&dev->lock);
		dev->running = true;
		mutex_unlock(&dev->lock);
		break;

	case CSI_DMA_IOCTL_STOP:
		mutex_lock(&dev->lock);
		dev->running = false;
		mutex_unlock(&dev->lock);
		break;

	case CSI_DMA_IOCTL_SET_NUM_EQ:
		if (copy_from_user(&neq, (void __user *)arg, sizeof(neq)))
			return -EFAULT;
		if (neq > 8)
			return -EINVAL;
		mutex_lock(&dev->lock);
		dev->num_eq = neq;
		dev->frame_symbols = CSI_FRAME_SYMBOLS(neq);
		dev->frame_bytes = CSI_FRAME_BYTES(neq);
		csi_reg_write(dev, SIDE_CH_REG_NUM_EQ_ADDR, neq);
		dev->meta->num_eq = neq;
		dev->meta->frame_bytes = dev->frame_bytes;
		mutex_unlock(&dev->lock);
		break;

	case CSI_DMA_IOCTL_GET_STATS:
		if (copy_to_user((void __user *)arg, dev->meta,
				 sizeof(*dev->meta)))
			return -EFAULT;
		break;

	default:
		return -ENOTTY;
	}
	return 0;
}

static const struct file_operations csi_dma_fops = {
	.owner = THIS_MODULE,
	.open = csi_dma_open,
	.release = csi_dma_release,
	.mmap = csi_dma_mmap,
	.poll = csi_dma_poll,
	.unlocked_ioctl = csi_dma_ioctl,
};

/* ------------------------------ platform -------------------------------- */

static const struct of_device_id csi_dma_of_ids[] = {
	{ .compatible = "sdr,side_ch", },
	{}
};
MODULE_DEVICE_TABLE(of, csi_dma_of_ids);

static int csi_dma_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct resource *io;
	struct csi_dma_dev *cdev;
	size_t header_size = CSI_RING_HEADER_SIZE;
	int i, ret;

	cdev = devm_kzalloc(dev, sizeof(*cdev), GFP_KERNEL);
	if (!cdev)
		return -ENOMEM;

	cdev->dev = dev;
	dev_set_drvdata(dev, cdev);

	io = platform_get_resource(pdev, IORESOURCE_MEM, 0);
	cdev->base = devm_ioremap_resource(dev, io);
	if (IS_ERR(cdev->base))
		return PTR_ERR(cdev->base);

	dev_info(dev, "%s: io 0x%llx, num_eq %d, slots %d x %d bytes\n",
		 CSI_DRV_NAME, (u64)io->start, num_eq_init,
		 num_slots_init, slot_size_init);

	/* ---- initialize the PL side channel (mirror of side_ch.c) ---- */
	csi_reg_write(cdev, SIDE_CH_REG_MULTI_RST_ADDR, 4);
	csi_reg_write(cdev, SIDE_CH_REG_CONFIG_ADDR, 0x7001);
	csi_reg_write(cdev, SIDE_CH_REG_IQ_TRIGGER_ADDR, 10);
	csi_reg_write(cdev, SIDE_CH_REG_NUM_EQ_ADDR, num_eq_init);
	csi_reg_write(cdev, SIDE_CH_REG_CONFIG_ADDR, 0x0001);

	for (i = 0; i < 8; i++)
		csi_reg_write(cdev, SIDE_CH_REG_MULTI_RST_ADDR, 0);
	for (i = 0; i < 32; i++)
		csi_reg_write(cdev, SIDE_CH_REG_MULTI_RST_ADDR, 0xffffffff);
	for (i = 0; i < 8; i++)
		csi_reg_write(cdev, SIDE_CH_REG_MULTI_RST_ADDR, 0);

	/* ---- DMA channel ---- */
	cdev->chan = dma_request_chan(dev, "tx_dma_s2mm");
	if (IS_ERR(cdev->chan)) {
		ret = PTR_ERR(cdev->chan);
		dev_err(dev, "dma_request_chan(tx_dma_s2mm) failed: %d\n", ret);
		return ret;
	}

	/* Zynq-7020 is a 32-bit DMA master (no SMMU) */
	ret = dma_set_mask_and_coherent(dev, DMA_BIT_MASK(32));
	if (ret) {
		dev_err(dev, "dma_set_mask_and_coherent(32) failed: %d\n", ret);
		goto err_chan;
	}

	/* ---- ring geometry ---- */
	cdev->num_slots = num_slots_init;
	cdev->slot_size = slot_size_init;
	cdev->num_eq = num_eq_init;
	cdev->frame_symbols = CSI_FRAME_SYMBOLS(num_eq_init);
	cdev->frame_bytes = CSI_FRAME_BYTES(num_eq_init);
	cdev->dma_buf_size = header_size +
			     (size_t)cdev->num_slots * cdev->slot_size;

	/* ---- coherent ring buffer (uncached, no SMMU on Zynq-7020) ---- */
	cdev->meta = dma_alloc_coherent(dev, cdev->dma_buf_size,
					&cdev->ring_dma, GFP_KERNEL);
	if (!cdev->meta) {
		dev_err(dev, "dma_alloc_coherent(%zu) failed\n",
			cdev->dma_buf_size);
		ret = -ENOMEM;
		goto err_chan;
	}
	cdev->ring = (u8 *)cdev->meta + header_size;

	/* ---- metadata header ---- */
	memset(cdev->meta, 0, sizeof(*cdev->meta));
	cdev->meta->magic = CSI_RING_MAGIC;
	cdev->meta->version = CSI_RING_VERSION;
	cdev->meta->num_slots = cdev->num_slots;
	cdev->meta->slot_size = cdev->slot_size;
	cdev->meta->frame_bytes = cdev->frame_bytes;
	cdev->meta->num_eq = cdev->num_eq;

	init_completion(&cdev->dma_cmp);
	init_waitqueue_head(&cdev->poll_wq);
	mutex_init(&cdev->lock);
	cdev->running = auto_start ? true : false;

	/* ---- misc device /dev/csi_dma ---- */
	cdev->misc.minor = MISC_DYNAMIC_MINOR;
	cdev->misc.name = CSI_DEV_NAME;
	cdev->misc.fops = &csi_dma_fops;
	cdev->misc.mode = 0666;
	ret = misc_register(&cdev->misc);
	if (ret) {
		dev_err(dev, "misc_register failed: %d\n", ret);
		goto err_dma;
	}

	/* ---- streaming kthread ---- */
	cdev->kthread = kthread_run(csi_dma_thread, cdev, "csi_dma_stream");
	if (IS_ERR(cdev->kthread)) {
		ret = PTR_ERR(cdev->kthread);
		dev_err(dev, "kthread_run failed: %d\n", ret);
		goto err_misc;
	}

	dev_info(dev, "%s: probe OK, frame %d bytes, buf %zu bytes\n",
		 CSI_DRV_NAME, cdev->frame_bytes, cdev->dma_buf_size);
	return 0;

err_misc:
	misc_deregister(&cdev->misc);
err_dma:
	dma_free_coherent(dev, cdev->dma_buf_size, cdev->meta, cdev->ring_dma);
err_chan:
	dma_release_channel(cdev->chan);
	return ret;
}

static int csi_dma_remove(struct platform_device *pdev)
{
	struct csi_dma_dev *cdev = dev_get_drvdata(&pdev->dev);

	if (cdev->kthread)
		kthread_stop(cdev->kthread);

	misc_deregister(&cdev->misc);

	if (cdev->chan)
		dmaengine_terminate_all(cdev->chan);
	if (cdev->meta)
		dma_free_coherent(&pdev->dev, cdev->dma_buf_size,
				  cdev->meta, cdev->ring_dma);
	if (cdev->chan)
		dma_release_channel(cdev->chan);

	dev_info(&pdev->dev, "%s: removed\n", CSI_DRV_NAME);
	return 0;
}

static struct platform_driver csi_dma_driver = {
	.driver = {
		.name = CSI_DRV_NAME,
		.owner = THIS_MODULE,
		.of_match_table = csi_dma_of_ids,
	},
	.probe = csi_dma_probe,
	.remove = csi_dma_remove,
};

module_platform_driver(csi_dma_driver);

MODULE_AUTHOR("(your name) <(your email)>");
MODULE_DESCRIPTION("openwifi zero-copy CSI streaming driver (V0)");
MODULE_LICENSE("GPL v2");

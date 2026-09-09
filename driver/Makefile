# kbuild Makefile for csi_dma.ko
#
# Normal (cross) build is done via scripts/build.sh which passes KDIR/ARCH/
# CROSS_COMPILE pointing at the board's adi-linux kernel.
#
#   make KDIR=/path/to/adi-linux ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf-

obj-m += csi_dma.o

ccflags-y := -I$(src)/../include

KDIR ?= /lib/modules/$(shell uname -r)/build

all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) modules

clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) clean

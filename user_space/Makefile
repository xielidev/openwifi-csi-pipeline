# Userspace tools
CC ?= gcc
CFLAGS ?= -O2 -Wall
CPPFLAGS += -I../include
LDLIBS += -lm

all: csi_bench

csi_bench: csi_bench.c
	$(CC) $(CFLAGS) $(CPPFLAGS) -o $@ $< $(LDLIBS)

clean:
	rm -f csi_bench

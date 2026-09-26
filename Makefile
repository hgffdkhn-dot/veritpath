# veritpath - plain make, no cmake, no autotools.
#
#   make                build build/veritpath
#   make static         fully static Linux binary (glibc or musl)
#   make android        cross build for Android with the NDK (see build-android.sh)
#   make install        install to $PREFIX (default /usr/local)
#   make clean
#
# Optional compression backends are auto-detected from the headers present:
#   lzma.h  -> xz/lzma    bzlib.h -> bzip2    zstd.h -> zstd
# zlib (gzip) is required - every libc and the Android NDK ship it.

CC      ?= cc
PREFIX  ?= /usr/local
CFLAGS  ?= -O2 -std=c11 -Wall -Wextra
LDFLAGS ?=
LDLIBS  ?= -lz

SRCDIR  := src
OBJDIR  := build
BINDIR  := build

SRCS := $(SRCDIR)/main.c $(SRCDIR)/util.c $(SRCDIR)/compress.c $(SRCDIR)/cpio.c \
        $(SRCDIR)/bootimg.c $(SRCDIR)/detect.c $(SRCDIR)/json.c \
        $(SRCDIR)/payload.c $(SRCDIR)/strategy.c
OBJS := $(SRCS:$(SRCDIR)/%.c=$(OBJDIR)/%.o)
TARGET := $(BINDIR)/veritpath

DEFS :=
ifneq ($(wildcard /usr/include/lzma.h /usr/local/include/lzma.h),)
DEFS += -DHAVE_LZMA
LDLIBS += -llzma
endif
ifneq ($(wildcard /usr/include/bzlib.h /usr/local/include/bzlib.h),)
DEFS += -DHAVE_BZIP2
LDLIBS += -lbz2
endif
ifneq ($(wildcard /usr/include/zstd.h /usr/local/include/zstd.h),)
DEFS += -DHAVE_ZSTD
LDLIBS += -lzstd
endif

ALL_CFLAGS := $(CFLAGS) $(DEFS) -I$(SRCDIR) -D_GNU_SOURCE

.PHONY: all static android install clean test

all: $(TARGET)

$(OBJDIR):
	@mkdir -p $(OBJDIR)

$(OBJDIR)/%.o: $(SRCDIR)/%.c | $(OBJDIR)
	$(CC) $(ALL_CFLAGS) -c $< -o $@

$(TARGET): $(OBJS)
	$(CC) $(LDFLAGS) $(OBJS) $(LDLIBS) -o $@
	@echo "built $@"
	@$(TARGET) --version

static:
	$(MAKE) clean
	$(MAKE) all LDFLAGS="-static"

android:
	bash build-android.sh

install: $(TARGET)
	install -d $(DESTDIR)$(PREFIX)/bin
	install -m 755 $(TARGET) $(DESTDIR)$(PREFIX)/bin/veritpath

test: $(TARGET)
	bash tests/run.sh

clean:
	rm -rf $(OBJDIR)

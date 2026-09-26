/* Architecture / layout detection. */
#include "vp.h"

#include <stdlib.h>

static const struct {
    int api;
    const char *ver;
} api_table[] = {
    {26, "8.0"}, {27, "8.1"}, {28, "9"}, {29, "10"}, {30, "11"},
    {31, "12"}, {32, "12L"}, {33, "13"}, {34, "14"}, {35, "15"}, {36, "16"},
};

static uint32_t rd32(const uint8_t *p, size_t off)
{
    return (uint32_t)(p[off] | (p[off + 1] << 8) | (p[off + 2] << 16) |
                      ((uint32_t)p[off + 3] << 24));
}

static void decode_os_version(uint32_t v, char *out, size_t outsz)
{
    if (!v) {
        snprintf(out, outsz, "unknown");
        return;
    }
    uint32_t ver = v >> 11;
    uint32_t patch = v & 0x7FF;
    uint32_t a = (ver >> 14) & 0x3F, b = (ver >> 7) & 0x7F, c = ver & 0x7F;
    if (ver) {
        snprintf(out, outsz, "%u.%u.%u", a, b, c);
        return;
    }
    (void)patch;
    snprintf(out, outsz, "unknown");
}

int detect_arch(const uint8_t *kernel, size_t klen, const uint8_t *dtb, size_t dlen,
                char *out, size_t outsz)
{
    snprintf(out, outsz, "unknown");
    if (kernel && klen > 0) {
        buf_t raw;
        buf_init(&raw);
        if (comp_decompress(kernel, klen, &raw) == 0 && raw.len) {
            if (raw.len > 0x40 && raw.data[0] == 'M' && raw.data[1] == 'Z' &&
                vp_memmem(raw.data, 0x60, "ARM64", 5) != NULL) {
                snprintf(out, outsz, "arm64");
                buf_free(&raw);
                return 0;
            }
            if (raw.len > 20 && raw.data[0] == 0x7f && memcmp(raw.data + 1, "ELF", 3) == 0) {
                static const struct {
                    uint16_t m;
                    const char *n;
                } machines[] = {
                    {3, "x86"}, {40, "arm"}, {62, "x86_64"}, {183, "arm64"},
                    {243, "riscv64"}, {258, "riscv64"},
                };
                uint16_t m = (uint16_t)(raw.data[18] | (raw.data[19] << 8));
                for (size_t i = 0; i < sizeof(machines) / sizeof(machines[0]); i++) {
                    if (machines[i].m == m) {
                        snprintf(out, outsz, "%s", machines[i].n);
                        buf_free(&raw);
                        return 0;
                    }
                }
            }
            if (raw.len > 0x28 && rd32(raw.data, 0x24) == 0x016F2818u) {
                snprintf(out, outsz, "arm");
                buf_free(&raw);
                return 0;
            }
            if (raw.len > 0x206 && memcmp(raw.data + 0x202, "HdrS", 4) == 0) {
                uint16_t xload = raw.len > 0x238
                                     ? (uint16_t)(raw.data[0x236] | (raw.data[0x237] << 8))
                                     : 0;
                snprintf(out, outsz, "%s", (xload & 1) ? "x86_64" : "x86");
                buf_free(&raw);
                return 0;
            }
        }
        buf_free(&raw);
    }
    if (dtb && dlen) {
        if (vp_memmem(dtb, dlen, "arm,armv8", 9) || vp_memmem(dtb, dlen, "arm,arm-v8", 10)) {
            snprintf(out, outsz, "arm64");
            return 0;
        }
        if (vp_memmem(dtb, dlen, "arm,v7", 6) || vp_memmem(dtb, dlen, "arm,arm1176", 11)) {
            snprintf(out, outsz, "arm");
            return 0;
        }
        if (vp_memmem(dtb, dlen, "riscv", 5)) {
            snprintf(out, outsz, "riscv64");
            return 0;
        }
    }
    return 0;
}

static const boot_img_t *find_img(image_set_t *set, size_t n, const char *role)
{
    for (size_t i = 0; i < n; i++) {
        if (set[i].img && strcmp(set[i].role, role) == 0)
            return set[i].img;
    }
    return NULL;
}

static void guess_api(image_set_t *set, size_t n, int *api, char *ver, size_t verz)
{
    const boot_img_t *boot = find_img(set, n, "boot");
    const boot_img_t *init_boot = find_img(set, n, "init_boot");
    const boot_img_t *vendor = find_img(set, n, "vendor_boot");
    const boot_img_t *ref = boot ? boot : init_boot;
    if (ref && ref->os_version) {
        char v[32];
        decode_os_version(ref->os_version, v, sizeof(v));
        if (strcmp(v, "unknown") != 0) {
            int major = atoi(v);
            for (size_t i = 0; i < sizeof(api_table) / sizeof(api_table[0]); i++) {
                if (atoi(api_table[i].ver) == major) {
                    *api = api_table[i].api;
                    snprintf(ver, verz, "%s", api_table[i].ver);
                    return;
                }
            }
            snprintf(ver, verz, "%.15s", v);
            *api = 0;
            return;
        }
    }
    if (init_boot) {
        *api = 33;
        snprintf(ver, verz, "13");
        return;
    }
    if (vendor && vendor->header_version >= 4) {
        *api = 31;
        snprintf(ver, verz, "12");
        return;
    }
    uint32_t hv = ref ? ref->header_version : 0;
    switch (hv) {
    case 4: *api = 31; snprintf(ver, verz, "12"); break;
    case 3: *api = 30; snprintf(ver, verz, "11"); break;
    case 2: *api = 29; snprintf(ver, verz, "10"); break;
    case 1: *api = 27; snprintf(ver, verz, "8.1"); break;
    default: *api = 25; snprintf(ver, verz, "7.1"); break;
    }
}

static int looks_system_as_root(const boot_img_t *img, int api)
{
    const char *cl = boot_img_cmdline((boot_img_t *)img);
    if (strstr(cl, "system_as_root"))
        return 1;
    cpio_archive_t a;
    cpio_init(&a);
    if (boot_img_ramdisk_archive((boot_img_t *)img, &a) == 0) {
        int has_system_init = cpio_find(&a, "system/bin/init") != NULL;
        int has_init = cpio_find(&a, "init") != NULL;
        int has_system_dir = cpio_find(&a, "system") != NULL;
        cpio_free(&a);
        if (has_system_init)
            return 0;
        if (has_init && has_system_dir)
            return 1;
        if (has_init)
            return 1;
    } else {
        cpio_free(&a);
    }
    return api >= 28;
}

void detect_analyze(image_set_t *set, size_t n, analysis_t *res)
{
    memset(res, 0, sizeof(*res));
    snprintf(res->arch, sizeof(res->arch), "unknown");
    snprintf(res->android_version, sizeof(res->android_version), "unknown");
    snprintf(res->layout, sizeof(res->layout), "unknown");
    res->android_api = 0;

    const boot_img_t *boot = find_img(set, n, "boot");
    const boot_img_t *init_boot = find_img(set, n, "init_boot");
    const boot_img_t *vendor = find_img(set, n, "vendor_boot");
    const boot_img_t *recovery = find_img(set, n, "recovery");
    res->boot = boot;
    res->init_boot = init_boot;
    res->vendor_boot = vendor;
    res->has_vendor_boot = vendor != NULL;

    /* arch: prefer an image that carries a kernel */
    const boot_img_t *ksrc = boot;
    if (!ksrc || !ksrc->kernel.len)
        ksrc = recovery;
    if (!ksrc || !ksrc->kernel.len)
        ksrc = init_boot;
    if (ksrc && ksrc->kernel.len) {
        buf_t *dtb = boot_img_dtb((boot_img_t *)ksrc);
        detect_arch(ksrc->kernel.data, ksrc->kernel.len, dtb ? dtb->data : NULL,
                    dtb ? dtb->len : 0, res->arch, sizeof(res->arch));
    } else if (vendor && vendor->dtb.len) {
        detect_arch(NULL, 0, vendor->dtb.data, vendor->dtb.len, res->arch,
                    sizeof(res->arch));
    }

    guess_api(set, n, &res->android_api, res->android_version,
              sizeof(res->android_version));

    /* where is the ramdisk */
    const boot_img_t *target = NULL;
    if (init_boot && init_boot->ramdisk.len) {
        target = init_boot;
        snprintf(res->layout, sizeof(res->layout), "init_boot");
        snprintf(res->target, sizeof(res->target), "init_boot");
    } else if (boot && boot->ramdisk.len) {
        target = boot;
        snprintf(res->layout, sizeof(res->layout), "boot");
        snprintf(res->target, sizeof(res->target), "boot");
    } else if (recovery && recovery->ramdisk.len) {
        target = recovery;
        snprintf(res->layout, sizeof(res->layout), "boot");
        snprintf(res->target, sizeof(res->target), "recovery");
    } else if (vendor && vendor->ramdisk.len) {
        target = vendor;
        snprintf(res->layout, sizeof(res->layout), "vendor_boot");
        snprintf(res->target, sizeof(res->target), "vendor_boot");
    }
    res->target_img = target;

    res->gki = (init_boot != NULL) ||
               (boot && boot->header_version >= 3 && !boot->ramdisk.len);
    if (vendor) {
        for (size_t i = 0; i < vendor->n_frags; i++) {
            if (vendor->frags[i].type == 2)
                res->recovery_fragment = 1;
        }
    }

    if (target) {
        res->system_as_root = looks_system_as_root(target, res->android_api);
        cpio_archive_t a;
        cpio_init(&a);
        if (boot_img_ramdisk_archive((boot_img_t *)target, &a) == 0) {
            res->n_segments = (int)a.n;
            res->already_patched = cpio_find(&a, "veritpath.json") != NULL ||
                                   cpio_find(&a, "init.veritpath.rc") != NULL;
            cpio_free(&a);
        }
    }

    /* A/B slot */
    const char *cl = target ? boot_img_cmdline((boot_img_t *)target) : "";
    const char *slot = strstr(cl, "androidboot.slot_suffix=");
    if (slot) {
        slot += strlen("androidboot.slot_suffix=");
        if (*slot == '_')
            slot++;
        snprintf(res->slot, sizeof(res->slot), "%c", *slot);
    }
}

static const char *layout_advice(const char *layout)
{
    if (strcmp(layout, "init_boot") == 0)
        return "Android 13+ GKI: the generic ramdisk lives in init_boot.img, "
               "boot.img only carries the kernel. Patch init_boot.img.";
    if (strcmp(layout, "vendor_boot") == 0)
        return "GKI 1.0 (Android 11/12): boot.img has no ramdisk, the generic "
               "ramdisk is the vendor_ramdisk inside vendor_boot.img. Patch it "
               "there and keep the fragment table in sync.";
    if (strcmp(layout, "boot") == 0)
        return "Ramdisk lives inside this image. Patch it directly; kernel and "
               "DTB stay untouched.";
    return "No ramdisk found. Supply init_boot.img (Android 13+) or a boot.img "
           "that actually contains one.";
}

static void print_finding(const char *key, const char *value)
{
    printf("  %-22s %s\n", key, value);
}

void detect_print(analysis_t *res, int as_json)
{
    if (as_json) {
        printf("{\n");
        printf("  \"arch\": \"%s\",\n", res->arch);
        printf("  \"android_api\": %d,\n", res->android_api);
        printf("  \"android_version\": \"%s\",\n", res->android_version);
        printf("  \"ramdisk_layout\": \"%s\",\n", res->layout);
        printf("  \"target\": \"%s\",\n", res->target);
        printf("  \"system_as_root\": %s,\n", res->system_as_root ? "true" : "false");
        printf("  \"gki\": %s,\n", res->gki ? "true" : "false");
        printf("  \"already_patched\": %s,\n", res->already_patched ? "true" : "false");
        printf("  \"ramdisk_segments\": %d\n", res->n_segments);
        printf("}\n");
        return;
    }
    printf("veritpath %s - boot image analysis\n", VP_VERSION);
    puts("==============================================================");
    print_finding("arch", res->arch);
    char ver[48];
    snprintf(ver, sizeof(ver), "%s (API %d)", res->android_version, res->android_api);
    print_finding("android_version", ver);
    print_finding("ramdisk_layout", res->layout);
    print_finding("system_as_root", res->system_as_root ? "true" : "false");
    print_finding("gki", res->gki ? "true" : "false");
    char seg[32];
    snprintf(seg, sizeof(seg), "%d", res->n_segments);
    print_finding("ramdisk_segments", seg);
    print_finding("already_patched", res->already_patched ? "true" : "false");
    if (res->slot[0])
        print_finding("slot", res->slot);

    if (res->boot)
        print_finding("boot.header_version",
                      (snprintf(seg, sizeof(seg), "%u", res->boot->header_version), seg));
    puts("--------------------------------------------------------------");
    printf("  . %s\n", layout_advice(res->layout));
    if (res->system_as_root)
        puts("  . system-as-root: the ramdisk is only the first-stage init, "
             "/system is mounted as '/'. Inject into the ramdisk, not /system.");
    else
        puts("  . legacy root layout: injected files stay visible in / after boot.");
    if (res->gki)
        puts("  . GKI device: never touch the kernel image - only the ramdisk is "
             "patched.");
    if (res->recovery_fragment)
        puts("  . vendor_boot holds a RECOVERY fragment: add --patch-vendor-boot "
             "if you need the payload inside recovery/fastbootd.");
    if (res->already_patched)
        puts("  ! this image already carries a veritpath payload (use --force to "
             "re-inject)");
    if (strcmp(res->arch, "unknown") == 0)
        puts("  ! architecture unknown: supply boot.img as well, init_boot.img "
             "alone carries no kernel");
    puts("--------------------------------------------------------------");
    printf("  injection target     %s\n", res->target[0] ? res->target : "(none)");
    if (res->has_vendor_boot && strcmp(res->target, "vendor_boot") != 0)
        puts("  optional targets     vendor_boot");
}

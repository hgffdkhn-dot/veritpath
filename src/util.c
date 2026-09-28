/* veritpath - Android boot image analyzer and payload injector.
 *
 * _GNU_SOURCE is defined here rather than on the command line so the sources
 * compile identically under -std=c11, -std=gnu11 and any cross toolchain:
 * glibc otherwise hides PATH_MAX, strtok_r, symlink, readlink and lstat.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE 1
#endif

#include <limits.h>
#include "vp.h"

#include <dirent.h>
#include <errno.h>
#include <stdarg.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

int vp_verbose = 0;

void vp_set_verbose(int on)
{
    vp_verbose = on;
}

static void emit(const char *prefix, const char *fmt, va_list ap)
{
    fputs(prefix, stderr);
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
}

void vp_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    emit("==> ", fmt, ap);
    va_end(ap);
}

void vp_info(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    emit("  . ", fmt, ap);
    va_end(ap);
}

void vp_warn(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    emit("  ! ", fmt, ap);
    va_end(ap);
}

void vp_dbg(const char *fmt, ...)
{
    if (!vp_verbose)
        return;
    va_list ap;
    va_start(ap, fmt);
    emit("  # ", fmt, ap);
    va_end(ap);
}

void vp_err(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    emit("veritpath: ", fmt, ap);
    va_end(ap);
}

void *xmalloc(size_t n)
{
    void *p = malloc(n ? n : 1);
    if (!p) {
        vp_err("out of memory");
        exit(2);
    }
    return p;
}

void *xrealloc(void *p, size_t n)
{
    void *q = realloc(p, n ? n : 1);
    if (!q) {
        vp_err("out of memory");
        exit(2);
    }
    return q;
}

char *xstrdup(const char *s)
{
    size_t n = strlen(s) + 1;
    char *p = xmalloc(n);
    memcpy(p, s, n);
    return p;
}

/* ------------------------------------------------------------------ buf_t */

void buf_init(buf_t *b)
{
    b->data = NULL;
    b->len = 0;
    b->cap = 0;
}

void buf_free(buf_t *b)
{
    free(b->data);
    buf_init(b);
}

void buf_reset(buf_t *b)
{
    b->len = 0;
}

int buf_reserve(buf_t *b, size_t extra)
{
    /* always keep room for the trailing NUL that buf_append*() writes */
    if (b->len + extra + 1 <= b->cap)
        return 0;
    size_t need = b->len + extra + 1;
    size_t cap = b->cap ? b->cap : 256;
    while (cap < need)
        cap *= 2;
    uint8_t *p = xrealloc(b->data, cap);
    b->data = p;
    b->cap = cap;
    return 0;
}

int buf_append(buf_t *b, const void *data, size_t len)
{
    if (!len)
        return 0;
    buf_reserve(b, len);
    memcpy(b->data + b->len, data, len);
    b->len += len;
    b->data[b->len] = 0;
    return 0;
}

int buf_append_str(buf_t *b, const char *s)
{
    return buf_append(b, s, strlen(s));
}

int buf_appendf(buf_t *b, const char *fmt, ...)
{
    char tmp[1024];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(tmp, sizeof(tmp), fmt, ap);
    va_end(ap);
    if (n < 0)
        return -1;
    if ((size_t)n >= sizeof(tmp))
        n = (int)sizeof(tmp) - 1;
    return buf_append(b, tmp, (size_t)n);
}

int buf_append_pad(buf_t *b, size_t align, uint8_t fill)
{
    if (!align)
        return 0;
    size_t rem = b->len % align;
    if (!rem)
        return 0;
    size_t need = align - rem;
    buf_reserve(b, need);
    memset(b->data + b->len, fill, need);
    b->len += need;
    b->data[b->len] = 0;
    return 0;
}

/* ------------------------------------------------------------------- misc */

void *vp_memmem(const void *hay, size_t haylen, const void *needle, size_t needlelen)
{
    if (!needlelen)
        return (void *)hay;
    if (haylen < needlelen)
        return NULL;
    const unsigned char *h = hay;
    const unsigned char *n = needle;
    for (size_t i = 0; i + needlelen <= haylen; i++) {
        if (h[i] == n[0] && memcmp(h + i, n, needlelen) == 0)
            return (void *)(h + i);
    }
    return NULL;
}

size_t round_up_sz(size_t v, size_t align)
{
    if (!align)
        return v;
    size_t rem = v % align;
    return rem ? v + (align - rem) : v;
}

const char *human_size(size_t n)
{
    static char bufs[4][32];
    static int idx = 0;
    char *out = bufs[idx = (idx + 1) & 3];
    double v = (double)n;
    static const char *units[] = {"B", "KiB", "MiB", "GiB"};
    int u = 0;
    while (v >= 1024.0 && u < 3) {
        v /= 1024.0;
        u++;
    }
    snprintf(out, 32, "%.1f%s", v, units[u]);
    return out;
}

int read_file(const char *path, buf_t *out)
{
    FILE *f = fopen(path, "rb");
    if (!f)
        return -1;
    buf_reset(out);
    uint8_t tmp[65536];
    size_t got;
    while ((got = fread(tmp, 1, sizeof(tmp), f)) > 0)
        buf_append(out, tmp, got);
    int bad = ferror(f);
    fclose(f);
    /* An empty file left out->data NULL, and callers hand it straight to
     * strlen()/memcmp() - which is UB for a null pointer. Always leave a
     * valid, NUL-terminated buffer behind. */
    if (!out->data) {
        buf_reserve(out, 0);
        if (out->data)
            out->data[0] = 0;
    }
    return bad ? -1 : 0;
}

int mkdir_p(const char *path)
{
    char *tmp = xstrdup(path);
    size_t n = strlen(tmp);
    for (size_t i = 1; i < n; i++) {
        if (tmp[i] == '/') {
            tmp[i] = 0;
            mkdir(tmp, 0755);
            tmp[i] = '/';
        }
    }
    int r = mkdir(tmp, 0755);
    free(tmp);
    return (r == 0 || errno == EEXIST) ? 0 : -1;
}

int write_file(const char *path, const void *data, size_t len)
{
    FILE *f = fopen(path, "wb");
    if (!f)
        return -1;
    size_t w = fwrite(data, 1, len, f);
    if (fclose(f) != 0)
        return -1;
    return w == len ? 0 : -1;
}

int file_exists(const char *path)
{
    struct stat st;
    return stat(path, &st) == 0;
}

int is_dir(const char *path)
{
    struct stat st;
    if (stat(path, &st) != 0)
        return 0;
    return S_ISDIR(st.st_mode);
}

/* directory part of a path ("" when there is none, "." handled by caller) */
char *path_dirname(const char *path)
{
    if (!path)
        return NULL;
    const char *slash = strrchr(path, '/');
    if (!slash)
        return xstrdup(".");
    size_t n = (size_t)(slash - path);
    if (n == 0)
        return xstrdup("/");
    char *out = xmalloc(n + 1);
    memcpy(out, path, n);
    out[n] = 0;
    return out;
}

/* is `dir` one of the colon separated entries of PATH? */
int dir_in_path(const char *dir, const char *path)
{
    if (!dir || !path)
        return 0;
    size_t dlen = strlen(dir);
    const char *p = path;
    while (*p) {
        const char *sep = strchr(p, ':');
        size_t len = sep ? (size_t)(sep - p) : strlen(p);
        if (len == dlen && strncmp(p, dir, len) == 0)
            return 1;
        if (!sep)
            break;
        p = sep + 1;
    }
    return 0;
}

char *path_join(const char *a, const char *b)
{
    size_t la = strlen(a), lb = strlen(b);
    int need_sep = la && a[la - 1] != '/' && lb && b[0] != '/';
    char *out = xmalloc(la + lb + 2);
    memcpy(out, a, la);
    if (need_sep)
        out[la++] = '/';
    memcpy(out + la, b, lb + 1);
    return out;
}

char *replace_suffix(const char *path, const char *suffix)
{
    const char *slash = strrchr(path, '/');
    const char *base = slash ? slash + 1 : path;
    const char *dot = strrchr(base, '.');
    size_t stem = dot ? (size_t)(dot - path) : strlen(path);
    size_t slen = strlen(suffix);
    char *out = xmalloc(stem + slen + 1);
    memcpy(out, path, stem);
    memcpy(out + stem, suffix, strlen(suffix) + 1);
    return out;
}

void vp_report_missing(const char *role, const char *path)
{
    char abs[PATH_MAX];
    if (!realpath(path, abs)) {
        /* realpath fails when the file is absent - build the path by hand */
        char cwd[PATH_MAX];
        if (!getcwd(cwd, sizeof(cwd)))
            snprintf(cwd, sizeof(cwd), "?");
        if (path[0] == '/') {
            size_t n = strlen(path);
            if (n >= sizeof(abs))
                n = sizeof(abs) - 1;
            memcpy(abs, path, n);
            abs[n] = 0;
        } else {
            size_t n = strlen(cwd);
            if (n >= sizeof(abs))
                n = sizeof(abs) - 1;
            memcpy(abs, cwd, n);
            abs[n] = 0;
            size_t room = sizeof(abs) - n - 1;
            size_t m = strlen(path);
            if (m > room)
                m = room;
            memcpy(abs + n, "/", 1);
            memcpy(abs + n + 1, path, m);
            abs[n + 1 + m] = 0;
        }
    }

    vp_err("%s: no such file: %s", role, path);
    fprintf(stderr, "  looked for : %s\n", abs);

    char cwd2[PATH_MAX];
    if (getcwd(cwd2, sizeof(cwd2)))
        fprintf(stderr, "  current dir: %s\n", cwd2);

    char dir[PATH_MAX];
    snprintf(dir, sizeof(dir), "%s", abs);
    char *slash = strrchr(dir, '/');
    if (slash) {
        *slash = 0;
        if (!*dir)
            snprintf(dir, sizeof(dir), "/");
    }

    DIR *d = opendir(dir);
    if (!d) {
        fprintf(stderr, "  %s does not exist either\n", dir);
        fprintf(stderr, "  on Termux, sdcard access needs:  termux-setup-storage\n");
        return;
    }
    fprintf(stderr, "  %s contains:\n", dir);
    int shown = 0;
    struct dirent *e;
    while ((e = readdir(d)) && shown < 12) {
        if (strcmp(e->d_name, ".") == 0 || strcmp(e->d_name, "..") == 0)
            continue;
        char full[PATH_MAX * 2];
        snprintf(full, sizeof(full), "%s/%s", dir, e->d_name);
        struct stat st;
        char size[32];
        if (stat(full, &st) == 0 && S_ISREG(st.st_mode)) {
            double kb = (double)st.st_size / 1024.0;
            snprintf(size, sizeof(size), "%.1fKiB", kb);
            fprintf(stderr, "      %-40s %s\n", e->d_name, size);
        } else {
            fprintf(stderr, "    d %s\n", e->d_name);
        }
        shown++;
    }
    closedir(d);
}

/* --------------------------------------------------------- output capture
 *
 * Embedders (the JNI binding, tests, other tools) need a command's output as a
 * string. Two things make this harder than it looks:
 *
 * 1. Errors go to stderr. Capturing only stdout means a failing command returns
 *    an empty string, which looks like "the tool produced nothing".
 * 2. tmpfile() is unreliable outside a normal Linux desktop: Android has no
 *    /tmp, TMPDIR is usually unset inside an app, and the current directory is
 *    typically "/" (not writable). tmpfile() then returns NULL and every call
 *    silently yields no output.
 *
 * So: search a list of candidate directories, and capture stdout *and* stderr
 * into the same file (they share one file description, so writes interleave
 * correctly).
 */
static int g_saved_out = -1;
static int g_saved_err = -1;
static int g_cap_fd = -1;
static FILE *g_cap_file = NULL;
static char *g_cap_dir = NULL;      /* explicit dir, set by the embedder */
static char g_cap_why[256];

void vp_capture_set_dir(const char *dir)
{
    free(g_cap_dir);
    g_cap_dir = (dir && *dir) ? xstrdup(dir) : NULL;
}

const char *vp_capture_error(void)
{
    return g_cap_why[0] ? g_cap_why : NULL;
}

/* Returns an fd for an anonymous scratch file, or -1. */
static int open_scratch(void)
{
    const char *cands[8];
    int n = 0;
    if (g_cap_dir)
        cands[n++] = g_cap_dir;
    const char *env = getenv("TMPDIR");
    if (env && *env)
        cands[n++] = env;
#ifdef P_tmpdir
    cands[n++] = P_tmpdir;
#endif
    cands[n++] = "/tmp";
    cands[n++] = "/data/local/tmp";
    cands[n++] = ".";

#if defined(_WIN32) || defined(_WIN64)
    /* mkstemp is not guaranteed on MinGW; tmpfile is fine there */
    FILE *f = tmpfile();
    if (!f)
        return -1;
    return dup(fileno(f));
#else
    for (int i = 0; i < n; i++) {
        if (!cands[i] || !cands[i][0])
            continue;
        size_t len = strlen(cands[i]) + 32;
        char *tpl = xmalloc(len);
        snprintf(tpl, len, "%s/.veritpath-XXXXXX", cands[i]);
        int fd = mkstemp(tpl);
        if (fd >= 0) {
            /* unlink straight away: nothing to clean up, even on a crash */
            unlink(tpl);
            free(tpl);
            return fd;
        }
        free(tpl);
    }
    return -1;
#endif
}

int vp_capture_start(void)
{
    if (g_cap_file)
        return -1;
    fflush(stdout);
    fflush(stderr);

    g_cap_why[0] = 0;
    int fd = open_scratch();
    if (fd < 0) {
        snprintf(g_cap_why, sizeof(g_cap_why),
                 "cannot create a scratch file for output capture; "
                 "call vp_capture_set_dir() with a writable directory");
        return -1;
    }
    FILE *f = fdopen(fd, "w+");
    if (!f) {
        close(fd);
        snprintf(g_cap_why, sizeof(g_cap_why), "cannot fdopen the scratch file");
        return -1;
    }
    g_cap_fd = fd;
    g_cap_file = f;
    g_saved_out = dup(STDOUT_FILENO);
    g_saved_err = dup(STDERR_FILENO);
    /* both point at the same file description, so they share one offset */
    dup2(fd, STDOUT_FILENO);
    dup2(fd, STDERR_FILENO);
    return 0;
}

char *vp_capture_stop(void)
{
    fflush(stdout);
    fflush(stderr);
    if (g_saved_err >= 0) {
        dup2(g_saved_err, STDERR_FILENO);
        close(g_saved_err);
        g_saved_err = -1;
    }
    if (g_saved_out >= 0) {
        dup2(g_saved_out, STDOUT_FILENO);
        close(g_saved_out);
        g_saved_out = -1;
    }
    if (!g_cap_file)
        return NULL;

    rewind(g_cap_file);
    size_t cap = 4096, len = 0;
    char *buf = xmalloc(cap);
    for (;;) {
        if (len + 1024 > cap) {
            cap *= 2;
            char *nb = realloc(buf, cap);
            if (!nb)
                break;
            buf = nb;
        }
        size_t n = fread(buf + len, 1, cap - len - 1, g_cap_file);
        len += n;
        if (n == 0)
            break;
    }
    buf[len] = 0;
    fclose(g_cap_file);
    g_cap_file = NULL;
    g_cap_fd = -1;
    return buf;
}

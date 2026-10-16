/* Public V4L2-only smoke: no frames are written to disk or stdout. */
#define _GNU_SOURCE
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/videodev2.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#define BUFFER_COUNT 8
#define FRAME_COUNT 40
static volatile sig_atomic_t interrupted;
struct mapping { void *address; size_t length; };
static void stop(int sig) { interrupted = sig; }
static int call(int fd, unsigned long op, void *arg) {
    int result;
    do { result = ioctl(fd, op, arg); } while (result < 0 && errno == EINTR && !interrupted);
    return result;
}
static bool contains(const char *text, const char *needle) {
    return strcasestr(text, needle) != NULL;
}
static int failure(const char *what) {
    int saved = errno;
    fprintf(stderr, "%s: %s\n", what, strerror(saved));
    return saved == EBUSY || saved == EACCES || saved == EPERM ? 78 : 1;
}
static void json_string(const char *s) {
    putchar('"');
    for (; *s; ++s) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') { putchar('\\'); putchar(c); }
        else if (c < 32) printf("\\u%04x", c);
        else putchar(c);
    }
    putchar('"');
}

int main(int argc, char **argv) {
    if (argc != 2 || (!contains(argv[1], "ov2740") && !contains(argv[1], "ov8858"))) {
        fprintf(stderr, "usage: camera_smoke ov2740|ov8858\n");
        return 2;
    }
    struct sigaction action = { .sa_handler = stop };
    sigemptyset(&action.sa_mask);
    sigaction(SIGTERM, &action, NULL);
    sigaction(SIGINT, &action, NULL);
    sigaction(SIGALRM, &action, NULL);
    alarm(25);
    int fd = -1, result = 1, selected = -1, original_input = -1;
    bool streaming = false, allocated = false, changed_input = false, changed_format = false;
    bool have_format = false;
    struct mapping maps[BUFFER_COUNT] = {{0}};
    struct v4l2_format original_format = { .type = V4L2_BUF_TYPE_VIDEO_CAPTURE };
    struct v4l2_format format = { .type = V4L2_BUF_TYPE_VIDEO_CAPTURE };
    char node[PATH_MAX] = "", input_names[64][33];
    unsigned int input_count = 0;
    unsigned int frames = 0, minimum_bytes = UINT_MAX, maximum_bytes = 0;
    DIR *directory = opendir("/sys/class/video4linux");
    if (!directory) return failure("opening video sysfs class");
    struct dirent *entry;
    int discovery_error = 0;
    while (!interrupted && (entry = readdir(directory))) {
        if (strncmp(entry->d_name, "video", 5) || !isdigit((unsigned char)entry->d_name[5])) continue;
        char path[PATH_MAX], name[256];
        snprintf(path, sizeof(path), "/sys/class/video4linux/%s/name", entry->d_name);
        FILE *file = fopen(path, "r");
        if (!file) continue;
        char *read_name = fgets(name, sizeof(name), file);
        fclose(file);
        if (!read_name || !contains(name, "atomisp")) continue;
        snprintf(path, sizeof(path), "/dev/%s", entry->d_name);
        int candidate = open(path, O_RDWR | O_NONBLOCK | O_CLOEXEC);
        if (candidate < 0) { discovery_error = errno; continue; }
        struct v4l2_capability cap = {0};
        if (call(candidate, VIDIOC_QUERYCAP, &cap) < 0) {
            discovery_error = errno; close(candidate); continue;
        }
        uint32_t caps = cap.capabilities & V4L2_CAP_DEVICE_CAPS ? cap.device_caps : cap.capabilities;
        if (!(caps & V4L2_CAP_VIDEO_CAPTURE) || !(caps & V4L2_CAP_STREAMING)) {
            close(candidate); continue;
        }
        for (unsigned int i = 0; i < 64; ++i) {
            struct v4l2_input input = { .index = i };
            if (call(candidate, VIDIOC_ENUMINPUT, &input) < 0) {
                if (errno != EINVAL) discovery_error = errno;
                break;
            }
            input.name[sizeof(input.name) - 1] = 0;
            memcpy(input_names[input_count], input.name, sizeof(input.name));
            input_names[input_count][32] = 0;
            ++input_count;
            if (contains((char *)input.name, argv[1])) selected = (int)i;
        }
        if (selected >= 0) { fd = candidate; snprintf(node, sizeof(node), "%s", path); break; }
        input_count = 0;
        close(candidate);
    }
    closedir(directory);
    if (fd < 0) {
        if (discovery_error) { errno = discovery_error; return failure("discovering AtomISP capture/input"); }
        fprintf(stderr, "no AtomISP capture node exposing sensor %s\n", argv[1]);
        return 1;
    }
    if (call(fd, VIDIOC_G_INPUT, &original_input) < 0) {
        if (errno != EINVAL && errno != ENOTTY) { result = failure("G_INPUT"); goto cleanup; }
        original_input = -1;
    }
    if (call(fd, VIDIOC_G_FMT, &original_format) == 0) have_format = true;
    else if (errno != EINVAL && errno != ENOTTY) { result = failure("G_FMT original"); goto cleanup; }
    if (call(fd, VIDIOC_S_INPUT, &selected) < 0) { result = failure("S_INPUT"); goto cleanup; }
    changed_input = original_input >= 0 && original_input != selected;
    bool front = contains(argv[1], "ov2740");
    format.fmt.pix.width = front ? 1916 : 1616;
    format.fmt.pix.height = front ? 1076 : 1208;
    format.fmt.pix.pixelformat = V4L2_PIX_FMT_YUYV;
    format.fmt.pix.field = V4L2_FIELD_NONE;
    if (call(fd, VIDIOC_S_FMT, &format) < 0) { result = failure("S_FMT YUYV"); goto cleanup; }
    changed_format = true;
    if (format.fmt.pix.pixelformat != V4L2_PIX_FMT_YUYV || !format.fmt.pix.width || !format.fmt.pix.height ||
        (uint64_t)format.fmt.pix.bytesperline < (uint64_t)format.fmt.pix.width * 2 ||
        (uint64_t)format.fmt.pix.sizeimage < (uint64_t)format.fmt.pix.bytesperline * format.fmt.pix.height) {
        fprintf(stderr, "invalid negotiated YUYV geometry/bounds\n"); goto cleanup;
    }
    struct v4l2_requestbuffers request = {
        .count = BUFFER_COUNT, .type = V4L2_BUF_TYPE_VIDEO_CAPTURE, .memory = V4L2_MEMORY_MMAP
    };
    if (call(fd, VIDIOC_REQBUFS, &request) < 0) { result = failure("REQBUFS"); goto cleanup; }
    allocated = true;
    if (request.count < BUFFER_COUNT) { fprintf(stderr, "fewer than eight MMAP buffers available\n"); goto cleanup; }
    for (unsigned int i = 0; i < BUFFER_COUNT; ++i) {
        struct v4l2_buffer buffer = {
            .type = V4L2_BUF_TYPE_VIDEO_CAPTURE, .memory = V4L2_MEMORY_MMAP, .index = i
        };
        if (call(fd, VIDIOC_QUERYBUF, &buffer) < 0) { result = failure("QUERYBUF"); goto cleanup; }
        if (buffer.length < format.fmt.pix.sizeimage) { fprintf(stderr, "MMAP buffer smaller than sizeimage\n"); goto cleanup; }
        maps[i].address = mmap(NULL, buffer.length, PROT_READ | PROT_WRITE, MAP_SHARED, fd, buffer.m.offset);
        if (maps[i].address == MAP_FAILED) { maps[i].address = NULL; result = failure("mmap"); goto cleanup; }
        maps[i].length = buffer.length;
        if (call(fd, VIDIOC_QBUF, &buffer) < 0) { result = failure("QBUF"); goto cleanup; }
    }
    enum v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (call(fd, VIDIOC_STREAMON, &type) < 0) { result = failure("STREAMON"); goto cleanup; }
    streaming = true;
    while (frames < FRAME_COUNT && !interrupted) {
        struct pollfd pollfd = { .fd = fd, .events = POLLIN };
        int ready = poll(&pollfd, 1, 2500);
        if (ready < 0 && errno == EINTR) continue;
        if (ready < 0) { result = failure("poll"); goto cleanup; }
        if (!ready) { fprintf(stderr, "frame poll timed out after 2500ms\n"); goto cleanup; }
        if (pollfd.revents & (POLLERR | POLLHUP | POLLNVAL)) { fprintf(stderr, "capture poll error flags 0x%x\n", pollfd.revents); goto cleanup; }
        struct v4l2_buffer buffer = { .type = type, .memory = V4L2_MEMORY_MMAP };
        if (call(fd, VIDIOC_DQBUF, &buffer) < 0) {
            if (errno == EAGAIN) continue;
            result = failure("DQBUF"); goto cleanup;
        }
        if (buffer.index >= BUFFER_COUNT || buffer.bytesused > maps[buffer.index].length ||
            buffer.bytesused > format.fmt.pix.sizeimage ||
            (uint64_t)buffer.bytesused < (uint64_t)format.fmt.pix.bytesperline * format.fmt.pix.height ||
            (buffer.flags & V4L2_BUF_FLAG_ERROR)) {
            fprintf(stderr, "invalid/error capture buffer index=%u bytes=%u flags=0x%x\n", buffer.index, buffer.bytesused, buffer.flags);
            goto cleanup;
        }
        if (buffer.bytesused < minimum_bytes) minimum_bytes = buffer.bytesused;
        if (buffer.bytesused > maximum_bytes) maximum_bytes = buffer.bytesused;
        ++frames;
        if (call(fd, VIDIOC_QBUF, &buffer) < 0) { result = failure("requeue"); goto cleanup; }
    }
    if (interrupted) { fprintf(stderr, "capture interrupted by signal %d\n", interrupted); goto cleanup; }
    result = 0;
cleanup:
    /* Cleanup is intentionally reached for partial setup and SIGTERM/SIGINT. */
    alarm(0);
    if (streaming) {
        enum v4l2_buf_type off_type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        if (ioctl(fd, VIDIOC_STREAMOFF, &off_type) < 0) { failure("cleanup STREAMOFF"); result = 1; }
    }
    for (unsigned int i = 0; i < BUFFER_COUNT; ++i) {
        if (maps[i].address && munmap(maps[i].address, maps[i].length) < 0) { failure("cleanup munmap"); result = 1; }
    }
    if (allocated) {
        struct v4l2_requestbuffers release = { .count = 0, .type = V4L2_BUF_TYPE_VIDEO_CAPTURE, .memory = V4L2_MEMORY_MMAP };
        if (ioctl(fd, VIDIOC_REQBUFS, &release) < 0) { failure("cleanup REQBUFS(0)"); result = 1; }
    }
    if (changed_input && ioctl(fd, VIDIOC_S_INPUT, &original_input) < 0) { failure("restore input"); result = 1; }
    if (have_format && changed_format && ioctl(fd, VIDIOC_S_FMT, &original_format) < 0) { failure("restore format"); result = 1; }
    if (close(fd) < 0) { failure("close capture"); result = 1; }
    if (!result) {
        printf("{\"node\":"); json_string(node);
        printf(",\"sensor\":"); json_string(argv[1]);
        printf(",\"inputs\":[");
        for (unsigned int i = 0; i < input_count; ++i) { if (i) putchar(','); json_string(input_names[i]); }
        printf("],\"frames\":%u,\"width\":%u,\"height\":%u,\"stride\":%u,\"bytes\":%u,\"minimum_bytesused\":%u,\"maximum_bytesused\":%u,\"buffers\":%d}\n",
            frames, format.fmt.pix.width, format.fmt.pix.height, format.fmt.pix.bytesperline,
            format.fmt.pix.sizeimage, minimum_bytes, maximum_bytes, BUFFER_COUNT);
    }
    return result;
}

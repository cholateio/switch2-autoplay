#include "proto.h"

#include <string.h>

#define HEADER_LEN 4
#define OVERHEAD (HEADER_LEN + 1)

uint8_t proto_crc8(const uint8_t *data, size_t n)
{
    uint8_t crc = 0;
    for (size_t i = 0; i < n; i++) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; bit++) {
            crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x07) : (uint8_t)(crc << 1);
        }
    }
    return crc;
}

size_t proto_encode(const proto_msg_t *m, uint8_t *out)
{
    if (m->len > PROTO_MAX_PAYLOAD) {
        return 0;
    }
    out[0] = PROTO_MAGIC;
    out[1] = m->len;
    out[2] = m->type;
    out[3] = m->seq;
    memcpy(out + HEADER_LEN, m->payload, m->len);
    out[HEADER_LEN + m->len] = proto_crc8(out + 1, (size_t)m->len + 3);
    return (size_t)m->len + OVERHEAD;
}

void proto_parser_init(proto_parser_t *p)
{
    p->count = 0;
    p->crc_errors = 0;
}

static void drop_front(proto_parser_t *p, uint8_t n)
{
    p->count = (uint8_t)(p->count - n);
    memmove(p->buf, p->buf + n, p->count);
}

static void count_error(proto_parser_t *p)
{
    if (p->crc_errors < UINT16_MAX) {
        p->crc_errors++;
    }
}

void proto_parser_feed(proto_parser_t *p, uint8_t byte)
{
    if (p->count == PROTO_MAX_FRAME) {
        // Only reachable if the caller skipped proto_parser_next(): a full
        // buffer always holds a frame that can be decided.
        drop_front(p, 1);
        count_error(p);
    }
    p->buf[p->count++] = byte;
}

bool proto_parser_next(proto_parser_t *p, proto_msg_t *out)
{
    for (;;) {
        uint8_t start = 0;
        while (start < p->count && p->buf[start] != PROTO_MAGIC) {
            start++;
        }
        if (start > 0) {
            drop_front(p, start);
        }
        if (p->count < 2) {
            return false;
        }
        uint8_t len = p->buf[1];
        if (len <= PROTO_MAX_PAYLOAD) {
            uint8_t total = (uint8_t)(len + OVERHEAD);
            if (p->count < total) {
                return false;
            }
            if (proto_crc8(p->buf + 1, (size_t)total - 2) == p->buf[total - 1]) {
                out->len = len;
                out->type = p->buf[2];
                out->seq = p->buf[3];
                memcpy(out->payload, p->buf + HEADER_LEN, len);
                drop_front(p, total);
                return true;
            }
        }
        // Not a frame: drop only this magic byte, so a real frame that starts
        // inside the bytes it claimed is still found.
        count_error(p);
        drop_front(p, 1);
    }
}

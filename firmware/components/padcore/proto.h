#pragma once

// Framing for the serial protocol in docs/protocol.md. The Python copy is
// src/autoplay/core/controller/protocol.py; both must decode any byte stream
// alike (tests/controller/test_conformance.py).

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define PROTO_MAGIC 0xA5
#define PROTO_MAX_PAYLOAD 16
#define PROTO_MAX_FRAME (PROTO_MAX_PAYLOAD + 5)

enum {
    PROTO_PING = 0x01,
    PROTO_SET_STATE = 0x02,
    PROTO_RELEASE_ALL = 0x03,
    PROTO_HELLO = 0x04,
    PROTO_ACK = 0x81,
    PROTO_DONE = 0x82,
    PROTO_BUSY = 0x83,
    PROTO_PONG = 0x84,
    PROTO_INFO = 0x85,
    PROTO_NAK = 0x86,
};

enum {
    PROTO_NAK_LENGTH = 1,
    PROTO_NAK_TYPE = 2,
    PROTO_NAK_VALUE = 3,
    PROTO_NAK_DROPPED = 4,
};

typedef struct {
    uint8_t type, seq, len;
    uint8_t payload[PROTO_MAX_PAYLOAD];
} proto_msg_t;

typedef struct {
    uint8_t buf[PROTO_MAX_FRAME];
    uint8_t count;
    // Frame starts discarded for a length above the limit or a CRC mismatch;
    // stops at UINT16_MAX.
    uint16_t crc_errors;
} proto_parser_t;

uint8_t proto_crc8(const uint8_t *data, size_t n);

// Writes the frame to `out` (room for PROTO_MAX_FRAME bytes) and returns its
// length, or 0 if `m->len` is above PROTO_MAX_PAYLOAD.
size_t proto_encode(const proto_msg_t *m, uint8_t *out);

void proto_parser_init(proto_parser_t *p);

// Adds one received byte. Call proto_parser_next() until it returns false
// before feeding the next byte; that keeps room for it.
void proto_parser_feed(proto_parser_t *p, uint8_t byte);

// Returns true and fills `out` for each frame the bytes fed so far complete.
// One byte can complete more than one frame after a resynchronisation.
bool proto_parser_next(proto_parser_t *p, proto_msg_t *out);

#include <string.h>

#include "check.h"
#include "proto.h"

static const uint8_t PING_1[] = {0xa5, 0x00, 0x01, 0x01, 0x12};
static const uint8_t A_FOR_80_MS[] = {0xa5, 0x09, 0x02, 0x02, 0x04, 0x00, 0x08,
                                      0x80, 0x80, 0x80, 0x80, 0x50, 0x00, 0x11};

#define MAX_OUT 16

typedef struct {
    proto_parser_t parser;
    proto_msg_t msgs[MAX_OUT];
    int count;
} rx_t;

static void rx_init(rx_t *rx)
{
    proto_parser_init(&rx->parser);
    rx->count = 0;
}

static void rx_feed(rx_t *rx, const uint8_t *data, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        proto_parser_feed(&rx->parser, data[i]);
        proto_msg_t msg;
        while (proto_parser_next(&rx->parser, &msg)) {
            CHECK(rx->count < MAX_OUT);
            rx->msgs[rx->count++] = msg;
        }
    }
}

static proto_msg_t make(uint8_t type, uint8_t seq, const uint8_t *payload, uint8_t len)
{
    proto_msg_t m = {.type = type, .seq = seq, .len = len};
    if (len > 0) {
        memcpy(m.payload, payload, len);
    }
    return m;
}

static bool same(const proto_msg_t *a, const proto_msg_t *b)
{
    return a->type == b->type && a->seq == b->seq && a->len == b->len &&
           memcmp(a->payload, b->payload, a->len) == 0;
}

static size_t put(uint8_t *out, const proto_msg_t *m)
{
    size_t n = proto_encode(m, out);
    CHECK(n == (size_t)m->len + 5);
    return n;
}

static proto_msg_t counting(uint8_t seq)
{
    uint8_t payload[9];
    for (int i = 0; i < 9; i++) {
        payload[i] = (uint8_t)(seq + i);
    }
    return make(PROTO_SET_STATE, seq, payload, 9);
}

static void test_crc8_check_value(void)
{
    CHECK(proto_crc8((const uint8_t *)"123456789", 9) == 0xF4);
}

static void test_encode_ping(void)
{
    uint8_t out[PROTO_MAX_FRAME];
    proto_msg_t m = make(PROTO_PING, 1, NULL, 0);
    CHECK(proto_encode(&m, out) == sizeof(PING_1));
    CHECK(memcmp(out, PING_1, sizeof(PING_1)) == 0);
}

static void test_encode_set_state(void)
{
    const uint8_t payload[] = {0x04, 0x00, 0x08, 0x80, 0x80, 0x80, 0x80, 0x50, 0x00};
    uint8_t out[PROTO_MAX_FRAME];
    proto_msg_t m = make(PROTO_SET_STATE, 2, payload, sizeof(payload));
    CHECK(proto_encode(&m, out) == sizeof(A_FOR_80_MS));
    CHECK(memcmp(out, A_FOR_80_MS, sizeof(A_FOR_80_MS)) == 0);
}

static void test_encode_ack_done_pong(void)
{
    const uint8_t ack[] = {0xa5, 0x00, 0x81, 0x02, 0xad};
    const uint8_t done[] = {0xa5, 0x00, 0x82, 0x02, 0x92};
    const uint8_t pong[] = {0xa5, 0x02, 0x84, 0x01, 0x01, 0x08, 0xeb};
    const uint8_t pong_payload[] = {1, 8};
    uint8_t out[PROTO_MAX_FRAME];

    proto_msg_t m = make(PROTO_ACK, 2, NULL, 0);
    CHECK(proto_encode(&m, out) == sizeof(ack) && memcmp(out, ack, sizeof(ack)) == 0);
    m = make(PROTO_DONE, 2, NULL, 0);
    CHECK(proto_encode(&m, out) == sizeof(done) && memcmp(out, done, sizeof(done)) == 0);
    m = make(PROTO_PONG, 1, pong_payload, 2);
    CHECK(proto_encode(&m, out) == sizeof(pong) && memcmp(out, pong, sizeof(pong)) == 0);
}

static void test_encode_rejects_len_over_16(void)
{
    uint8_t out[PROTO_MAX_FRAME + 1];
    proto_msg_t m = {.type = PROTO_PING, .seq = 1, .len = PROTO_MAX_PAYLOAD + 1};
    CHECK(proto_encode(&m, out) == 0);
}

static void test_parser_handles_split_and_concatenated_frames(void)
{
    const uint8_t payload[] = {0, 1, 2, 3, 4, 5, 6, 7, 8};
    proto_msg_t msgs[3] = {
        make(PROTO_PING, 1, NULL, 0),
        make(PROTO_SET_STATE, 2, payload, sizeof(payload)),
        make(PROTO_HELLO, 3, NULL, 0),
    };
    uint8_t stream[3 * PROTO_MAX_FRAME];
    size_t n = 0;
    for (int i = 0; i < 3; i++) {
        n += put(stream + n, &msgs[i]);
    }

    rx_t rx;
    rx_init(&rx);
    rx_feed(&rx, stream, n);
    CHECK(rx.count == 3);
    for (int i = 0; i < 3; i++) {
        CHECK(same(&rx.msgs[i], &msgs[i]));
    }
    CHECK(rx.parser.crc_errors == 0);
}

static void test_parser_resyncs_after_garbage_and_bad_crc(void)
{
    const uint8_t garbage[] = "\x00\xff\xa5" "ESP-ROM:esp32s3\r\n";
    uint8_t bad[sizeof(PING_1)];
    memcpy(bad, PING_1, sizeof(PING_1));
    bad[sizeof(bad) - 1] ^= 0xFF;

    rx_t rx;
    rx_init(&rx);
    rx_feed(&rx, garbage, sizeof(garbage) - 1);
    rx_feed(&rx, bad, sizeof(bad));
    rx_feed(&rx, PING_1, sizeof(PING_1));
    CHECK(rx.count == 1);
    CHECK(rx.msgs[0].type == PROTO_PING && rx.msgs[0].seq == 1 && rx.msgs[0].len == 0);
    CHECK(rx.parser.crc_errors >= 1);
}

static void test_parser_rejects_len_over_16(void)
{
    // A frame that would be valid if a 17-byte payload were allowed.
    uint8_t oversized[17 + 5] = {0xa5, 17, PROTO_PING, 1};
    oversized[sizeof(oversized) - 1] = proto_crc8(oversized + 1, sizeof(oversized) - 2);

    rx_t rx;
    rx_init(&rx);
    rx_feed(&rx, oversized, sizeof(oversized));
    CHECK(rx.count == 0);
    rx_feed(&rx, PING_1, sizeof(PING_1));
    CHECK(rx.count == 1);
    CHECK(rx.parser.crc_errors >= 1);
}

static void test_corrupted_len_does_not_swallow_next_frame(void)
{
    uint8_t stream[3 * PROTO_MAX_FRAME];
    size_t n = sizeof(PING_1);
    memcpy(stream, PING_1, n);
    stream[1] = 9;
    proto_msg_t second = make(PROTO_PING, 2, NULL, 0);
    proto_msg_t third = make(PROTO_PING, 3, NULL, 0);
    n += put(stream + n, &second);
    n += put(stream + n, &third);

    rx_t rx;
    rx_init(&rx);
    rx_feed(&rx, stream, n);
    CHECK(rx.count == 2);
    CHECK(same(&rx.msgs[0], &second) && same(&rx.msgs[1], &third));
}

static void test_truncated_frame_then_valid_frame(void)
{
    const uint8_t payload[] = {0x00, 0x00, 0x06, 0x80, 0x80, 0x80, 0x80, 0x10, 0x00};
    proto_msg_t later = make(PROTO_SET_STATE, 3, payload, sizeof(payload));
    uint8_t frame[PROTO_MAX_FRAME];
    size_t n = put(frame, &later);

    rx_t rx;
    rx_init(&rx);
    rx_feed(&rx, A_FOR_80_MS, 7);
    CHECK(rx.count == 0);
    rx_feed(&rx, frame, n);
    CHECK(rx.count == 1);
    CHECK(same(&rx.msgs[0], &later));
}

static void test_inserted_and_deleted_byte_recovery(void)
{
    proto_msg_t first = counting(1), middle = counting(2), last = counting(3);
    uint8_t raw[PROTO_MAX_FRAME], stream[4 * PROTO_MAX_FRAME];
    size_t raw_n = put(raw, &middle);

    size_t n = put(stream, &first);
    memcpy(stream + n, raw, 5);
    stream[n + 5] = 0x77;
    memcpy(stream + n + 6, raw + 5, raw_n - 5);
    n += raw_n + 1;
    n += put(stream + n, &last);

    rx_t rx;
    rx_init(&rx);
    rx_feed(&rx, stream, n);
    CHECK(rx.count == 2);
    CHECK(same(&rx.msgs[0], &first) && same(&rx.msgs[1], &last));

    n = put(stream, &first);
    memcpy(stream + n, raw, 5);
    memcpy(stream + n + 5, raw + 6, raw_n - 6);
    n += raw_n - 1;
    n += put(stream + n, &last);

    rx_init(&rx);
    rx_feed(&rx, stream, n);
    CHECK(rx.count == 2);
    CHECK(same(&rx.msgs[0], &first) && same(&rx.msgs[1], &last));
}

static void test_stray_magic_byte_directly_before_a_frame(void)
{
    // A lone magic byte glued to the front of a frame must cost only itself.
    const uint8_t stray = PROTO_MAGIC;
    rx_t rx;
    rx_init(&rx);
    rx_feed(&rx, &stray, 1);
    rx_feed(&rx, PING_1, sizeof(PING_1));
    rx_feed(&rx, &stray, 1);
    rx_feed(&rx, A_FOR_80_MS, sizeof(A_FOR_80_MS));
    CHECK(rx.count == 2);
    CHECK(rx.msgs[0].type == PROTO_PING && rx.msgs[1].type == PROTO_SET_STATE);
}

static void test_error_counter_stops_at_its_maximum(void)
{
    rx_t rx;
    rx_init(&rx);
    const uint8_t bad_start[] = {0xa5, 0xff};
    for (int i = 0; i < 70000; i++) {
        rx_feed(&rx, bad_start, sizeof(bad_start));
    }
    CHECK(rx.parser.crc_errors == UINT16_MAX);
}

int main(void)
{
    RUN(test_crc8_check_value);
    RUN(test_encode_ping);
    RUN(test_encode_set_state);
    RUN(test_encode_ack_done_pong);
    RUN(test_encode_rejects_len_over_16);
    RUN(test_parser_handles_split_and_concatenated_frames);
    RUN(test_parser_resyncs_after_garbage_and_bad_crc);
    RUN(test_parser_rejects_len_over_16);
    RUN(test_corrupted_len_does_not_swallow_next_frame);
    RUN(test_truncated_frame_then_valid_frame);
    RUN(test_inserted_and_deleted_byte_recovery);
    RUN(test_stray_magic_byte_directly_before_a_frame);
    RUN(test_error_counter_stops_at_its_maximum);
    return 0;
}

// Command-line face of proto.c for tests/controller/test_conformance.py.
//   proto_cli                              decode stdin: one "type seq hexpayload" line per
//                                          frame, then "crc_errors=N"
//   proto_cli --encode TYPE SEQ [HEX]      print the frame as hex
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "proto.h"

static void print_hex(const uint8_t *data, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        printf("%02x", data[i]);
    }
}

static int encode(int argc, char **argv)
{
    if (argc < 4 || argc > 5) {
        fprintf(stderr, "usage: proto_cli --encode TYPE SEQ [HEXPAYLOAD]\n");
        return 2;
    }
    const char *hex = argc == 5 ? argv[4] : "";
    size_t hex_len = strlen(hex);
    if (hex_len % 2 != 0 || hex_len / 2 > PROTO_MAX_PAYLOAD) {
        fprintf(stderr, "proto_cli: payload must be at most %d whole bytes of hex\n", PROTO_MAX_PAYLOAD);
        return 2;
    }
    proto_msg_t m = {
        .type = (uint8_t)strtoul(argv[2], NULL, 0),
        .seq = (uint8_t)strtoul(argv[3], NULL, 0),
        .len = (uint8_t)(hex_len / 2),
    };
    for (size_t i = 0; i < m.len; i++) {
        unsigned byte;
        if (sscanf(hex + 2 * i, "%2x", &byte) != 1) {
            fprintf(stderr, "proto_cli: bad hex payload\n");
            return 2;
        }
        m.payload[i] = (uint8_t)byte;
    }
    uint8_t out[PROTO_MAX_FRAME];
    print_hex(out, proto_encode(&m, out));
    printf("\n");
    return 0;
}

static int decode(void)
{
    proto_parser_t parser;
    proto_parser_init(&parser);
    int c;
    while ((c = getchar()) != EOF) {
        proto_parser_feed(&parser, (uint8_t)c);
        proto_msg_t m;
        while (proto_parser_next(&parser, &m)) {
            printf("%u %u ", m.type, m.seq);
            print_hex(m.payload, m.len);
            printf("\n");
        }
    }
    printf("crc_errors=%u\n", parser.crc_errors);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc >= 2 && strcmp(argv[1], "--encode") == 0) {
        return encode(argc, argv);
    }
    if (argc != 1) {
        fprintf(stderr, "usage: proto_cli [--encode TYPE SEQ [HEXPAYLOAD]]\n");
        return 2;
    }
    return decode();
}

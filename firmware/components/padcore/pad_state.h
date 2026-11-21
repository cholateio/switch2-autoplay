#pragma once

#include <stdint.h>

// Field order and meaning match the SET_STATE payload in docs/protocol.md.
typedef struct {
    uint16_t buttons;
    uint8_t hat, lx, ly, rx, ry;
} pad_state_t;

#define PAD_STATE_NEUTRAL ((pad_state_t){0, 8, 128, 128, 128, 128})

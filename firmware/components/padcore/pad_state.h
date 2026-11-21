#pragma once

#include <stdbool.h>
#include <stdint.h>

// Field order and meaning match the SET_STATE payload in docs/protocol.md.
typedef struct {
    uint16_t buttons;
    uint8_t hat, lx, ly, rx, ry;
} pad_state_t;

#define PAD_STATE_NEUTRAL ((pad_state_t){0, 8, 128, 128, 128, 128})

static inline bool pad_state_equal(const pad_state_t *a, const pad_state_t *b)
{
    return a->buttons == b->buttons && a->hat == b->hat && a->lx == b->lx && a->ly == b->ly &&
           a->rx == b->rx && a->ry == b->ry;
}

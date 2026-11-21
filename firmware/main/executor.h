#pragma once

// Owns the pad queue: ticks it every millisecond, sends the current state to
// the gamepad backend and reports finished states over the serial link. The
// functions below are the only way other tasks touch the queue.

#include <stdbool.h>
#include <stdint.h>

#include "pad_state.h"
#include "padq.h"

// PONG and INFO `flags` bits (docs/protocol.md).
#define EXECUTOR_FLAG_USB_MOUNTED 0x01
#define EXECUTOR_FLAG_WD_TRIPPED 0x02

typedef struct {
    uint8_t flags;
    uint8_t queue_free;
    uint16_t wd_trips;
} executor_status_t;

// Call once, after hid_backend_init() and before any other function here.
void executor_start(void);

uint32_t executor_now_ms(void);

padq_push_t executor_push(uint8_t seq, const pad_state_t *s, uint16_t hold_ms);
void executor_release_all(void);
void executor_reset_session(void);
void executor_feed_watchdog(void);

// With `clear_wd_flag`, EXECUTOR_FLAG_WD_TRIPPED is reported and then cleared.
void executor_status(bool clear_wd_flag, executor_status_t *out);

#pragma once

// The queue of gamepad states and its timing rules (docs/protocol.md, "Queue
// and timing", "Duplicates and lost replies", "Watchdog"). It has no clock of
// its own: every call that depends on time takes `now_ms`, a millisecond
// counter that may wrap.

#include <stdbool.h>
#include <stdint.h>

#include "pad_state.h"

#define PADQ_DEPTH 8
#define PADQ_MIN_HOLD_MS 16
#define PADQ_WATCHDOG_MS 300
#define PADQ_SEQ_HISTORY 16

typedef enum {
    PADQ_OK,
    PADQ_BUSY,
    PADQ_DUP_PENDING,
    PADQ_DUP_DONE,
    PADQ_DUP_DROPPED,
} padq_push_t;

typedef struct {
    bool state_changed;  // `state` differs from the previous tick's
    pad_state_t state;   // the state to show now
    bool done;           // the hold time of `done_seq` ended on this tick
    uint8_t done_seq;
    bool wd_tripped;     // the watchdog released everything on this tick
} padq_out_t;

typedef struct {
    uint8_t seq;
    uint16_t hold_ms;
    pad_state_t state;
} padq_entry_t;

typedef struct {
    uint8_t seq;
    uint8_t status;
} padq_seen_t;

typedef struct {
    // Unfinished entries, oldest first, starting at `head`. While `active`,
    // the oldest one is the state being shown.
    padq_entry_t entries[PADQ_DEPTH];
    uint8_t head, count;
    bool active;
    bool delivered;
    uint32_t hold_start_ms;

    pad_state_t state;
    pad_state_t reported;

    padq_seen_t seen[PADQ_SEQ_HISTORY];
    uint8_t seen_count, seen_next;

    bool wd_armed;
    uint32_t wd_fed_ms;
} padq_t;

void padq_init(padq_t *q, uint32_t now_ms);

// Accepts one state. A seq that is still remembered is never queued again;
// the PADQ_DUP_* result tells what became of it.
padq_push_t padq_push(padq_t *q, uint8_t seq, const pad_state_t *s, uint16_t hold_ms);

// The host has taken a report carrying `delivered`. Starts the hold clock if
// that is the state being shown and its clock has not started yet.
void padq_mark_delivered(padq_t *q, const pad_state_t *delivered, uint32_t now_ms);

// Drops every unfinished entry and makes the state neutral.
void padq_release_all(padq_t *q, uint32_t now_ms);

// padq_release_all() plus forgetting every remembered seq.
void padq_reset_session(padq_t *q, uint32_t now_ms);

// A frame with a correct CRC arrived.
void padq_feed_watchdog(padq_t *q, uint32_t now_ms);

// Advances the queue; call every millisecond.
void padq_tick(padq_t *q, uint32_t now_ms, padq_out_t *out);

// How many more states padq_push() would accept now.
uint8_t padq_free(const padq_t *q);

#pragma once

// Tracks the one input report a gamepad backend has handed to the USB stack
// and the host has not taken yet. The stack frees the endpoint before it
// reports completion, so "endpoint free" alone does not mean the previous
// report's completion has been handled; this slot does.
//
// Not thread-safe: the backend serialises calls with its own lock.

#include <stdbool.h>
#include <stdint.h>

#include "pad_state.h"

// An endpoint that stays free this long while a report is still recorded as
// in flight lost that report without a completion (bus reset, unplug).
#define HIDSLOT_STALE_MS 50

typedef struct {
    bool in_flight;
    pad_state_t state;
    bool idle_seen;
    uint32_t idle_since_ms;
} hidslot_t;

// True if a new report may be reserved. `endpoint_free` is the stack's own
// view of the IN endpoint; call this regularly so a lost report is noticed.
bool hidslot_free(hidslot_t *slot, bool endpoint_free, uint32_t now_ms);

// Records `state` as in flight; false if another report still is.
bool hidslot_reserve(hidslot_t *slot, const pad_state_t *state);

// The stack refused or failed the report that was reserved.
void hidslot_cancel(hidslot_t *slot);

// The host took a report. True, with its state in `delivered`, if one was in
// flight.
bool hidslot_complete(hidslot_t *slot, pad_state_t *delivered);

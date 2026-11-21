#include "hidslot.h"

bool hidslot_free(hidslot_t *slot, bool endpoint_free, uint32_t now_ms)
{
    if (!slot->in_flight) {
        return true;
    }
    if (!endpoint_free) {
        slot->idle_seen = false;
        return false;
    }
    if (!slot->idle_seen) {
        slot->idle_seen = true;
        slot->idle_since_ms = now_ms;
        return false;
    }
    if (now_ms - slot->idle_since_ms < HIDSLOT_STALE_MS) {
        return false;
    }
    hidslot_cancel(slot);
    return true;
}

bool hidslot_reserve(hidslot_t *slot, const pad_state_t *state)
{
    if (slot->in_flight) {
        return false;
    }
    slot->in_flight = true;
    slot->idle_seen = false;
    slot->state = *state;
    return true;
}

void hidslot_cancel(hidslot_t *slot)
{
    slot->in_flight = false;
    slot->idle_seen = false;
}

bool hidslot_complete(hidslot_t *slot, pad_state_t *delivered)
{
    if (!slot->in_flight) {
        return false;
    }
    *delivered = slot->state;
    hidslot_cancel(slot);
    return true;
}

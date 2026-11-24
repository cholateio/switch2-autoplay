#include "padq.h"

#include <stddef.h>

enum { SEEN_PENDING, SEEN_DONE, SEEN_DROPPED };

static padq_seen_t *find_seen(padq_t *q, uint8_t seq)
{
    for (uint8_t i = 0; i < q->seen_count; i++) {
        if (q->seen[i].seq == seq) {
            return &q->seen[i];
        }
    }
    return NULL;
}

static void set_seen(padq_t *q, uint8_t seq, uint8_t status)
{
    padq_seen_t *seen = find_seen(q, seq);
    if (seen != NULL) {
        seen->status = status;
    }
}

// Overwrites the oldest remembered seq once the history is full. An unfinished
// entry is never the one overwritten: at most PADQ_DEPTH entries are
// unfinished and they are always the most recently accepted.
_Static_assert(PADQ_SEQ_HISTORY >= PADQ_DEPTH, "history must cover every unfinished entry");

static void remember(padq_t *q, uint8_t seq)
{
    q->seen[q->seen_next] = (padq_seen_t){.seq = seq, .status = SEEN_PENDING};
    q->seen_next = (uint8_t)((q->seen_next + 1) % PADQ_SEQ_HISTORY);
    if (q->seen_count < PADQ_SEQ_HISTORY) {
        q->seen_count++;
    }
}

static void drop_all(padq_t *q)
{
    for (uint8_t i = 0; i < q->count; i++) {
        set_seen(q, q->entries[(q->head + i) % PADQ_DEPTH].seq, SEEN_DROPPED);
    }
    q->head = 0;
    q->count = 0;
    q->active = false;
    q->delivered = false;
    q->state = PAD_STATE_NEUTRAL;
}

void padq_init(padq_t *q, uint32_t now_ms)
{
    *q = (padq_t){
        .state = PAD_STATE_NEUTRAL,
        .reported = PAD_STATE_NEUTRAL,
        .wd_fed_ms = now_ms,
    };
}

padq_push_t padq_push(padq_t *q, uint8_t seq, const pad_state_t *s, uint16_t hold_ms)
{
    const padq_seen_t *seen = find_seen(q, seq);
    if (seen != NULL) {
        switch (seen->status) {
        case SEEN_PENDING:
            return PADQ_DUP_PENDING;
        case SEEN_DONE:
            return PADQ_DUP_DONE;
        default:
            return PADQ_DUP_DROPPED;
        }
    }
    if (q->count == PADQ_DEPTH) {
        return PADQ_BUSY;
    }
    q->entries[(q->head + q->count) % PADQ_DEPTH] =
        (padq_entry_t){.seq = seq, .hold_ms = hold_ms, .state = *s};
    q->count++;
    remember(q, seq);
    return PADQ_OK;
}

void padq_mark_delivered(padq_t *q, const pad_state_t *delivered, uint32_t now_ms)
{
    if (q->active && !q->delivered && pad_state_equal(delivered, &q->state)) {
        q->delivered = true;
        q->hold_start_ms = now_ms;
    }
}

void padq_release_all(padq_t *q, uint32_t now_ms)
{
    (void)now_ms;
    drop_all(q);
}

void padq_reset_session(padq_t *q, uint32_t now_ms)
{
    (void)now_ms;
    drop_all(q);
    q->seen_count = 0;
    q->seen_next = 0;
}

void padq_feed_watchdog(padq_t *q, uint32_t now_ms)
{
    q->wd_armed = true;
    q->wd_fed_ms = now_ms;
}

void padq_tick(padq_t *q, uint32_t now_ms, padq_out_t *out)
{
    *out = (padq_out_t){0};

    // Unsigned subtraction keeps both intervals right across a counter wrap.
    if (q->wd_armed && now_ms - q->wd_fed_ms >= PADQ_WATCHDOG_MS) {
        drop_all(q);
        q->wd_armed = false;
        out->wd_tripped = true;
    }

    if (q->active && q->delivered) {
        const padq_entry_t *entry = &q->entries[q->head];
        uint32_t hold_ms = entry->hold_ms < PADQ_MIN_HOLD_MS ? PADQ_MIN_HOLD_MS : entry->hold_ms;
        if (now_ms - q->hold_start_ms >= hold_ms) {
            out->done = true;
            out->done_seq = entry->seq;
            set_seen(q, entry->seq, SEEN_DONE);
            q->head = (uint8_t)((q->head + 1) % PADQ_DEPTH);
            q->count--;
            q->active = false;
        }
    }

    if (!q->active && q->count > 0) {
        q->state = q->entries[q->head].state;
        q->active = true;
        q->delivered = false;
    }

    out->state = q->state;
    out->state_changed = !pad_state_equal(&q->state, &q->reported);
    q->reported = q->state;
}

uint8_t padq_free(const padq_t *q)
{
    return (uint8_t)(PADQ_DEPTH - q->count);
}

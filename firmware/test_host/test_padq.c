#include "check.h"
#include "padq.h"

#define A 0x0004
#define B 0x0002

typedef struct {
    int dones;
    uint8_t done_seq[32];
    uint32_t done_at[32];
    int wd_trips;
    int changes;
    pad_state_t state;
} run_t;

static pad_state_t pressed(uint16_t buttons)
{
    pad_state_t s = PAD_STATE_NEUTRAL;
    s.buttons = buttons;
    return s;
}

static bool is_neutral(const pad_state_t *s)
{
    pad_state_t neutral = PAD_STATE_NEUTRAL;
    return pad_state_equal(s, &neutral);
}

// Ticks once per millisecond over [from, to]. With `polled`, a host takes a
// report carrying the current state right after every tick.
static run_t run_ex(padq_t *q, uint32_t from, uint32_t to, bool polled)
{
    run_t r = {0};
    for (uint32_t t = from;; t++) {
        padq_out_t out;
        padq_tick(q, t, &out);
        if (out.done) {
            CHECK(r.dones < 32);
            r.done_seq[r.dones] = out.done_seq;
            r.done_at[r.dones] = t;
            r.dones++;
        }
        r.wd_trips += out.wd_tripped;
        r.changes += out.state_changed;
        r.state = out.state;
        if (polled) {
            padq_mark_delivered(q, &out.state, t);
        }
        if (t == to) {
            break;
        }
    }
    return r;
}

static run_t run(padq_t *q, uint32_t from, uint32_t to)
{
    return run_ex(q, from, to, false);
}

static run_t run_polled(padq_t *q, uint32_t from, uint32_t to)
{
    return run_ex(q, from, to, true);
}

static void push_ok(padq_t *q, uint8_t seq, uint16_t buttons, uint16_t hold_ms)
{
    pad_state_t s = pressed(buttons);
    CHECK(padq_push(q, seq, &s, hold_ms) == PADQ_OK);
}

static padq_push_t push_again(padq_t *q, uint8_t seq)
{
    pad_state_t s = pressed(B);
    return padq_push(q, seq, &s, 0);
}

static void test_state_holds_at_least_16ms_when_hold_is_0(void)
{
    padq_t q;
    padq_init(&q, 0);
    push_ok(&q, 1, A, 0);
    run_t r = run(&q, 0, 5);
    CHECK(r.state.buttons == A);
    pad_state_t a = pressed(A);
    padq_mark_delivered(&q, &a, 5);
    CHECK(run(&q, 6, 20).dones == 0);
    r = run(&q, 21, 21);
    CHECK(r.dones == 1 && r.done_seq[0] == 1);
}

static void test_done_emitted_at_hold_expiry_with_seq(void)
{
    padq_t q;
    padq_init(&q, 0);
    push_ok(&q, 7, A, 80);
    run(&q, 0, 10);
    pad_state_t a = pressed(A);
    padq_mark_delivered(&q, &a, 10);
    CHECK(run(&q, 11, 89).dones == 0);
    run_t r = run(&q, 90, 90);
    CHECK(r.dones == 1 && r.done_seq[0] == 7);
}

static void test_hold_clock_does_not_start_until_delivered(void)
{
    padq_t q;
    padq_init(&q, 0);
    push_ok(&q, 1, A, 10);
    push_ok(&q, 2, B, 10);
    run_t r = run(&q, 0, 500);
    CHECK(r.dones == 0);
    CHECK(r.state.buttons == A);
    CHECK(padq_free(&q) == PADQ_DEPTH - 2);

    pad_state_t a = pressed(A);
    padq_mark_delivered(&q, &a, 500);
    CHECK(run(&q, 501, 515).dones == 0);
    r = run(&q, 516, 516);
    CHECK(r.dones == 1 && r.done_seq[0] == 1);
    CHECK(r.state.buttons == B);
}

static void test_delivery_of_a_different_state_does_not_start_clock(void)
{
    padq_t q;
    padq_init(&q, 0);
    push_ok(&q, 1, A, 20);
    run(&q, 0, 0);
    pad_state_t other = PAD_STATE_NEUTRAL;
    padq_mark_delivered(&q, &other, 1);
    CHECK(run(&q, 1, 100).dones == 0);

    pad_state_t a = pressed(A);
    padq_mark_delivered(&q, &a, 100);
    CHECK(run(&q, 101, 119).dones == 0);
    CHECK(run(&q, 120, 120).dones == 1);
}

static void test_later_deliveries_do_not_restart_the_clock(void)
{
    padq_t q;
    padq_init(&q, 0);
    push_ok(&q, 1, A, 50);
    run_t r = run_polled(&q, 0, 60);
    CHECK(r.dones == 1 && r.done_at[0] == 50);
}

static void test_dup_of_pending_seq_returns_dup_pending(void)
{
    padq_t q;
    padq_init(&q, 0);
    push_ok(&q, 5, A, 100);
    CHECK(push_again(&q, 5) == PADQ_DUP_PENDING);
    CHECK(padq_free(&q) == PADQ_DEPTH - 1);
    run(&q, 0, 0);
    CHECK(push_again(&q, 5) == PADQ_DUP_PENDING);
    CHECK(padq_free(&q) == PADQ_DEPTH - 1);
}

static void test_dup_of_done_seq_returns_dup_done(void)
{
    padq_t q;
    padq_init(&q, 0);
    push_ok(&q, 5, A, 20);
    CHECK(run_polled(&q, 0, 30).dones == 1);
    CHECK(push_again(&q, 5) == PADQ_DUP_DONE);
    run_t r = run_polled(&q, 31, 200);
    CHECK(r.dones == 0);
    CHECK(r.state.buttons == A);
}

static void test_dup_of_dropped_seq_returns_dup_dropped(void)
{
    padq_t q;
    padq_init(&q, 0);
    padq_feed_watchdog(&q, 0);
    push_ok(&q, 5, A, 1000);
    CHECK(run_polled(&q, 0, PADQ_WATCHDOG_MS).wd_trips == 1);
    CHECK(push_again(&q, 5) == PADQ_DUP_DROPPED);
}

static void test_seq_history_keeps_last_16(void)
{
    padq_t q;
    padq_init(&q, 0);
    uint32_t t = 0;
    for (uint8_t seq = 1; seq <= PADQ_SEQ_HISTORY + 1; seq++) {
        push_ok(&q, seq, A, 0);
        CHECK(run_polled(&q, t, t + 20).dones == 1);
        t += 21;
    }
    for (uint8_t seq = 2; seq <= PADQ_SEQ_HISTORY + 1; seq++) {
        CHECK(push_again(&q, seq) == PADQ_DUP_DONE);
    }
    CHECK(push_again(&q, 1) == PADQ_OK);
}

static void test_state_persists_after_done_when_queue_empty(void)
{
    padq_t q;
    padq_init(&q, 0);
    push_ok(&q, 1, A, 20);
    run_t r = run_polled(&q, 0, 1000);
    CHECK(r.dones == 1);
    CHECK(r.state.buttons == A);
    CHECK(padq_free(&q) == PADQ_DEPTH);
}

static void test_queue_runs_in_order_each_with_min_hold(void)
{
    padq_t q;
    padq_init(&q, 0);
    push_ok(&q, 1, A, 0);
    push_ok(&q, 2, B, 0);
    push_ok(&q, 3, A | B, 0);
    run_t r = run_polled(&q, 0, 100);
    CHECK(r.dones == 3);
    CHECK(r.done_seq[0] == 1 && r.done_seq[1] == 2 && r.done_seq[2] == 3);
    CHECK(r.done_at[0] >= PADQ_MIN_HOLD_MS);
    CHECK(r.done_at[1] - r.done_at[0] >= PADQ_MIN_HOLD_MS);
    CHECK(r.done_at[2] - r.done_at[1] >= PADQ_MIN_HOLD_MS);
    CHECK(r.state.buttons == (A | B));
}

static void test_ninth_push_returns_busy(void)
{
    padq_t q;
    padq_init(&q, 0);
    for (uint8_t seq = 1; seq <= PADQ_DEPTH; seq++) {
        push_ok(&q, seq, A, 0);
    }
    CHECK(padq_free(&q) == 0);
    pad_state_t s = pressed(B);
    CHECK(padq_push(&q, 9, &s, 0) == PADQ_BUSY);
    // The state being shown still occupies one of the eight places.
    run(&q, 0, 0);
    CHECK(padq_push(&q, 9, &s, 0) == PADQ_BUSY);
    // A refused command is not remembered: it is taken once there is room.
    CHECK(run_polled(&q, 1, 20).dones == 1);
    CHECK(padq_push(&q, 9, &s, 0) == PADQ_OK);
}

static void test_release_all_clears_queue_and_neutralizes_now(void)
{
    padq_t q;
    padq_init(&q, 0);
    push_ok(&q, 1, A, 1000);
    push_ok(&q, 2, B, 1000);
    CHECK(run(&q, 0, 0).state.buttons == A);
    padq_release_all(&q, 1);
    CHECK(padq_free(&q) == PADQ_DEPTH);
    run_t r = run(&q, 1, 1);
    CHECK(is_neutral(&r.state));
    CHECK(r.changes == 1);
}

static void test_release_all_marks_active_and_queued_seqs_dropped(void)
{
    padq_t q;
    padq_init(&q, 0);
    for (uint8_t seq = 1; seq <= 4; seq++) {
        push_ok(&q, seq, A, 50);
    }
    run_polled(&q, 0, 10);
    padq_release_all(&q, 10);
    for (uint8_t seq = 1; seq <= 4; seq++) {
        CHECK(push_again(&q, seq) == PADQ_DUP_DROPPED);
    }
    run_t r = run_polled(&q, 11, 1000);
    CHECK(r.dones == 0);
    CHECK(is_neutral(&r.state));
}

static void test_watchdog_releases_and_clears_queue(void)
{
    padq_t q;
    padq_init(&q, 0);
    padq_feed_watchdog(&q, 0);
    push_ok(&q, 1, A, 2000);
    push_ok(&q, 2, B, 2000);
    run_t r = run_polled(&q, 0, PADQ_WATCHDOG_MS - 1);
    CHECK(r.wd_trips == 0 && r.state.buttons == A);

    r = run_polled(&q, PADQ_WATCHDOG_MS, PADQ_WATCHDOG_MS);
    CHECK(r.wd_trips == 1);
    CHECK(is_neutral(&r.state));
    CHECK(padq_free(&q) == PADQ_DEPTH);

    r = run_polled(&q, PADQ_WATCHDOG_MS + 1, 5000);
    CHECK(r.dones == 0 && r.wd_trips == 0);
    CHECK(is_neutral(&r.state));
}

static void test_watchdog_not_tripped_while_fed_every_100ms(void)
{
    padq_t q;
    padq_init(&q, 0);
    push_ok(&q, 1, A, 2000);
    int dones = 0, trips = 0;
    for (uint32_t t = 0; t < 2100; t += 100) {
        padq_feed_watchdog(&q, t);
        run_t r = run_polled(&q, t, t + 99);
        dones += r.dones;
        trips += r.wd_trips;
    }
    CHECK(trips == 0);
    CHECK(dones == 1);
}

static void test_watchdog_waits_for_the_first_frame(void)
{
    padq_t q;
    padq_init(&q, 0);
    CHECK(run(&q, 0, 2000).wd_trips == 0);
}

static void test_watchdog_trips_once_per_silence(void)
{
    padq_t q;
    padq_init(&q, 0);
    padq_feed_watchdog(&q, 0);
    CHECK(run(&q, 0, 1000).wd_trips == 1);
    padq_feed_watchdog(&q, 1000);
    CHECK(run(&q, 1001, 1000 + PADQ_WATCHDOG_MS - 1).wd_trips == 0);
    CHECK(run(&q, 1000 + PADQ_WATCHDOG_MS, 2000).wd_trips == 1);
}

static void test_reset_session_forgets_dedup(void)
{
    padq_t q;
    padq_init(&q, 0);
    push_ok(&q, 5, A, 0);
    CHECK(run_polled(&q, 0, 20).dones == 1);
    push_ok(&q, 6, B, 1000);
    run_polled(&q, 21, 30);

    padq_reset_session(&q, 30);
    run_t r = run_polled(&q, 31, 31);
    CHECK(is_neutral(&r.state));
    CHECK(padq_free(&q) == PADQ_DEPTH);
    CHECK(push_again(&q, 5) == PADQ_OK);
    CHECK(push_again(&q, 6) == PADQ_OK);
}

static void test_state_change_is_reported_once(void)
{
    padq_t q;
    padq_init(&q, 0);
    CHECK(run(&q, 0, 0).changes == 0);
    push_ok(&q, 1, A, 100);
    CHECK(run(&q, 1, 1).changes == 1);
    CHECK(run(&q, 2, 50).changes == 0);
}

static void test_hold_survives_clock_wraparound(void)
{
    const uint32_t start = UINT32_MAX - 40;
    padq_t q;
    padq_init(&q, start);
    padq_feed_watchdog(&q, start);
    push_ok(&q, 1, A, 80);
    run_t r = run_polled(&q, start, start + 79);
    CHECK(r.dones == 0 && r.wd_trips == 0);
    r = run_polled(&q, start + 80, start + 80);
    CHECK(r.dones == 1);
}

int main(void)
{
    RUN(test_state_holds_at_least_16ms_when_hold_is_0);
    RUN(test_done_emitted_at_hold_expiry_with_seq);
    RUN(test_hold_clock_does_not_start_until_delivered);
    RUN(test_delivery_of_a_different_state_does_not_start_clock);
    RUN(test_later_deliveries_do_not_restart_the_clock);
    RUN(test_dup_of_pending_seq_returns_dup_pending);
    RUN(test_dup_of_done_seq_returns_dup_done);
    RUN(test_dup_of_dropped_seq_returns_dup_dropped);
    RUN(test_seq_history_keeps_last_16);
    RUN(test_state_persists_after_done_when_queue_empty);
    RUN(test_queue_runs_in_order_each_with_min_hold);
    RUN(test_ninth_push_returns_busy);
    RUN(test_release_all_clears_queue_and_neutralizes_now);
    RUN(test_release_all_marks_active_and_queued_seqs_dropped);
    RUN(test_watchdog_releases_and_clears_queue);
    RUN(test_watchdog_not_tripped_while_fed_every_100ms);
    RUN(test_watchdog_waits_for_the_first_frame);
    RUN(test_watchdog_trips_once_per_silence);
    RUN(test_reset_session_forgets_dedup);
    RUN(test_state_change_is_reported_once);
    RUN(test_hold_survives_clock_wraparound);
    return 0;
}

#include "check.h"
#include "hidslot.h"

static pad_state_t pressed(uint16_t buttons)
{
    pad_state_t s = PAD_STATE_NEUTRAL;
    s.buttons = buttons;
    return s;
}

static void test_completion_returns_the_reserved_state(void)
{
    hidslot_t slot = {0};
    pad_state_t a = pressed(0x0004), out;
    CHECK(hidslot_free(&slot, true, 0));
    CHECK(hidslot_reserve(&slot, &a));
    CHECK(hidslot_complete(&slot, &out));
    CHECK(pad_state_equal(&out, &a));
    CHECK(hidslot_free(&slot, true, 1));
}

static void test_no_second_report_until_the_first_completes(void)
{
    hidslot_t slot = {0};
    pad_state_t a = pressed(0x0004), b = pressed(0x0002), out;
    CHECK(hidslot_reserve(&slot, &a));
    // The stack has already freed the endpoint, but the completion for `a`
    // has not been handled: a new report here would be mistaken for it.
    CHECK(!hidslot_free(&slot, true, 0));
    CHECK(!hidslot_reserve(&slot, &b));
    CHECK(hidslot_complete(&slot, &out));
    CHECK(pad_state_equal(&out, &a));
    CHECK(hidslot_reserve(&slot, &b));
}

static void test_completion_without_a_report_is_ignored(void)
{
    hidslot_t slot = {0};
    pad_state_t out;
    CHECK(!hidslot_complete(&slot, &out));
}

static void test_cancel_frees_the_slot(void)
{
    hidslot_t slot = {0};
    pad_state_t a = pressed(0x0004), out;
    CHECK(hidslot_reserve(&slot, &a));
    hidslot_cancel(&slot);
    CHECK(hidslot_free(&slot, true, 0));
    CHECK(!hidslot_complete(&slot, &out));
}

static void test_a_lost_report_expires_once_the_endpoint_stays_free(void)
{
    hidslot_t slot = {0};
    pad_state_t a = pressed(0x0004), out;
    CHECK(hidslot_reserve(&slot, &a));
    CHECK(!hidslot_free(&slot, true, 1000));
    CHECK(!hidslot_free(&slot, true, 1000 + HIDSLOT_STALE_MS - 1));
    CHECK(hidslot_free(&slot, true, 1000 + HIDSLOT_STALE_MS));
    CHECK(!hidslot_complete(&slot, &out));
}

static void test_a_waiting_report_never_expires_while_the_endpoint_is_busy(void)
{
    hidslot_t slot = {0};
    pad_state_t a = pressed(0x0004), out;
    CHECK(hidslot_reserve(&slot, &a));
    for (uint32_t t = 0; t < 10000; t += 7) {
        CHECK(!hidslot_free(&slot, false, t));
    }
    CHECK(hidslot_complete(&slot, &out));
}

static void test_the_expiry_clock_restarts_when_the_endpoint_is_busy_again(void)
{
    hidslot_t slot = {0};
    pad_state_t a = pressed(0x0004);
    CHECK(hidslot_reserve(&slot, &a));
    CHECK(!hidslot_free(&slot, true, 0));
    CHECK(!hidslot_free(&slot, false, 40));
    CHECK(!hidslot_free(&slot, true, 45));
    CHECK(!hidslot_free(&slot, true, 60));
    CHECK(hidslot_free(&slot, true, 45 + HIDSLOT_STALE_MS));
}

static void test_expiry_survives_clock_wraparound(void)
{
    hidslot_t slot = {0};
    pad_state_t a = pressed(0x0004);
    CHECK(hidslot_reserve(&slot, &a));
    CHECK(!hidslot_free(&slot, true, UINT32_MAX - 10));
    CHECK(!hidslot_free(&slot, true, 5));
    CHECK(hidslot_free(&slot, true, HIDSLOT_STALE_MS - 11));
}

int main(void)
{
    RUN(test_completion_returns_the_reserved_state);
    RUN(test_no_second_report_until_the_first_completes);
    RUN(test_completion_without_a_report_is_ignored);
    RUN(test_cancel_frees_the_slot);
    RUN(test_a_lost_report_expires_once_the_endpoint_stays_free);
    RUN(test_a_waiting_report_never_expires_while_the_endpoint_is_busy);
    RUN(test_the_expiry_clock_restarts_when_the_endpoint_is_busy_again);
    RUN(test_expiry_survives_clock_wraparound);
    return 0;
}

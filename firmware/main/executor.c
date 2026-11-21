#include "executor.h"

#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "hid_backend.h"
#include "proto.h"
#include "uart_link.h"

#define TICK_PERIOD_MS 1
#define TASK_STACK 4096
// Above the serial and USB tasks: this loop is what keeps the hold times.
#define TASK_PRIORITY 10

// One mutex guards the queue and the two watchdog fields; it is taken by this
// task, the serial receive task and the USB task (delivered callback), and
// never held across I/O.
static SemaphoreHandle_t s_mutex;
static padq_t s_q;
static uint16_t s_wd_trips;
static bool s_wd_flag;

static void lock(void)
{
    xSemaphoreTake(s_mutex, portMAX_DELAY);
}

static void unlock(void)
{
    xSemaphoreGive(s_mutex);
}

uint32_t executor_now_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

static void on_delivered(const pad_state_t *delivered)
{
    lock();
    padq_mark_delivered(&s_q, delivered, executor_now_ms());
    unlock();
}

static void executor_task(void *arg)
{
    (void)arg;
    TickType_t last_wake = xTaskGetTickCount();
    for (;;) {
        padq_out_t out;
        lock();
        padq_tick(&s_q, executor_now_ms(), &out);
        if (out.wd_tripped) {
            s_wd_flag = true;
            if (s_wd_trips < UINT16_MAX) {
                s_wd_trips++;
            }
        }
        unlock();

        if (out.done) {
            proto_msg_t done = {.type = PROTO_DONE, .seq = out.done_seq};
            uart_link_send(&done);
        }
        // Sent on every tick the endpoint is free, so the host always gets the
        // current state at its own polling rate.
        if (hid_backend_ready()) {
            hid_backend_send(&out.state);
        }
        xTaskDelayUntil(&last_wake, pdMS_TO_TICKS(TICK_PERIOD_MS));
    }
}

void executor_start(void)
{
    s_mutex = xSemaphoreCreateMutex();
    configASSERT(s_mutex != NULL);
    padq_init(&s_q, executor_now_ms());
    hid_backend_set_delivered_cb(on_delivered);
    BaseType_t ok = xTaskCreatePinnedToCore(executor_task, "executor", TASK_STACK, NULL, TASK_PRIORITY, NULL, 0);
    configASSERT(ok == pdPASS);
}

padq_push_t executor_push(uint8_t seq, const pad_state_t *s, uint16_t hold_ms)
{
    lock();
    padq_push_t result = padq_push(&s_q, seq, s, hold_ms);
    unlock();
    return result;
}

void executor_release_all(void)
{
    lock();
    padq_release_all(&s_q, executor_now_ms());
    unlock();
}

void executor_reset_session(void)
{
    lock();
    padq_reset_session(&s_q, executor_now_ms());
    unlock();
}

void executor_feed_watchdog(void)
{
    lock();
    padq_feed_watchdog(&s_q, executor_now_ms());
    unlock();
}

void executor_status(bool clear_wd_flag, executor_status_t *out)
{
    uint8_t flags = hid_backend_mounted() ? EXECUTOR_FLAG_USB_MOUNTED : 0;
    lock();
    if (s_wd_flag) {
        flags |= EXECUTOR_FLAG_WD_TRIPPED;
    }
    if (clear_wd_flag) {
        s_wd_flag = false;
    }
    out->queue_free = padq_free(&s_q);
    out->wd_trips = s_wd_trips;
    unlock();
    out->flags = flags;
}

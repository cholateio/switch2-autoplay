#include "esp_err.h"
#include "hid_backend.h"
#include "sdkconfig.h"

#if CONFIG_PADFW_DEMO

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define TICK_PERIOD_MS 1
#define DEMO_PERIOD_MS 1000
#define DEMO_PRESS_MS 80
#define HAT_RIGHT 2

// Ignores the serial link: D-pad right for 80 ms once per second.
static void run(void)
{
    TickType_t last_wake = xTaskGetTickCount();
    const TickType_t start = last_wake;
    for (;;) {
        pad_state_t state = PAD_STATE_NEUTRAL;
        if (pdTICKS_TO_MS(xTaskGetTickCount() - start) % DEMO_PERIOD_MS < DEMO_PRESS_MS) {
            state.hat = HAT_RIGHT;
        }
        if (hid_backend_ready()) {
            hid_backend_send(&state);
        }
        xTaskDelayUntil(&last_wake, pdMS_TO_TICKS(TICK_PERIOD_MS));
    }
}

#else

#include "executor.h"
#include "uart_link.h"

static void run(void)
{
    // The link is started second: a command must never arrive before the
    // executor exists.
    executor_start();
    uart_link_start();
}

#endif

void app_main(void)
{
    ESP_ERROR_CHECK(hid_backend_init());
    run();
}

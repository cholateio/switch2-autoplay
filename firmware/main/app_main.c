#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hid_backend.h"
#include "sdkconfig.h"

#define TICK_PERIOD_MS 1
#define DEMO_PERIOD_MS 1000
#define DEMO_PRESS_MS 80
#define HAT_RIGHT 2

static pad_state_t state_at(uint32_t elapsed_ms)
{
    pad_state_t state = PAD_STATE_NEUTRAL;
#if CONFIG_PADFW_DEMO
    if (elapsed_ms % DEMO_PERIOD_MS < DEMO_PRESS_MS) {
        state.hat = HAT_RIGHT;
    }
#else
    (void)elapsed_ms;
#endif
    return state;
}

void app_main(void)
{
    ESP_ERROR_CHECK(hid_backend_init());

    TickType_t last_wake = xTaskGetTickCount();
    const TickType_t start = last_wake;
    for (;;) {
        pad_state_t state = state_at(pdTICKS_TO_MS(xTaskGetTickCount() - start));
        if (hid_backend_ready()) {
            hid_backend_send(&state);
        }
        xTaskDelayUntil(&last_wake, pdMS_TO_TICKS(TICK_PERIOD_MS));
    }
}

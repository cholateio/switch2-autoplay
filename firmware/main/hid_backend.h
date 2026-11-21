#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "pad_state.h"

// One gamepad identity presented to the console. A different identity replaces
// the implementation file only; this interface stays.

esp_err_t hid_backend_init(void);

// True when the host has configured the device, the IN endpoint is free and
// the delivered callback for the previous report has been given its state.
bool hid_backend_ready(void);

// Queue one input report; false if the backend is not ready.
bool hid_backend_send(const pad_state_t *s);

// The backend byte reported in INFO (docs/protocol.md): 1 = pokken.
uint8_t hid_backend_id(void);

// `cb` runs on the USB task each time the host has taken a report, with the
// state that report carried. A report cancelled by a bus reset or an unplug is
// not delivered.
void hid_backend_set_delivered_cb(void (*cb)(const pad_state_t *delivered));

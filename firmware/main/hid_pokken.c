// The HORI Pokken Tournament DX Pro Pad identity: a wired pad the Switch
// accepts with no handshake. Descriptor values follow progmem/Switch-Fightstick
// (Descriptors.c) and were compared byte for byte with GP2040-CE
// (SwitchDescriptors.h); the endpoint interval is the original pad's 5 ms.
#include "class/hid/hid_device.h"
#include "freertos/FreeRTOS.h"
#include "hid_backend.h"
#include "tinyusb.h"
#include "tinyusb_default_config.h"

#define POKKEN_BACKEND_ID 1
#define REPORT_LEN 8
#define EP_IN 0x81
#define EP_OUT 0x02
#define EP_SIZE 64
#define EP_INTERVAL_MS 5

static const uint8_t s_report_desc[] = {
    0x05, 0x01,        // Usage Page (Generic Desktop)
    0x09, 0x05,        // Usage (Game Pad)
    0xA1, 0x01,        // Collection (Application)
    0x15, 0x00,        //   Logical Minimum (0)
    0x25, 0x01,        //   Logical Maximum (1)
    0x35, 0x00,        //   Physical Minimum (0)
    0x45, 0x01,        //   Physical Maximum (1)
    0x75, 0x01,        //   Report Size (1)
    0x95, 0x10,        //   Report Count (16)
    0x05, 0x09,        //   Usage Page (Button)
    0x19, 0x01,        //   Usage Minimum (1)
    0x29, 0x10,        //   Usage Maximum (16)
    0x81, 0x02,        //   Input (Data,Var,Abs)
    0x05, 0x01,        //   Usage Page (Generic Desktop)
    0x25, 0x07,        //   Logical Maximum (7)
    0x46, 0x3B, 0x01,  //   Physical Maximum (315)
    0x75, 0x04,        //   Report Size (4)
    0x95, 0x01,        //   Report Count (1)
    0x65, 0x14,        //   Unit (English Rotation: Degrees)
    0x09, 0x39,        //   Usage (Hat switch)
    0x81, 0x42,        //   Input (Data,Var,Abs,Null State)
    0x65, 0x00,        //   Unit (None)
    0x95, 0x01,        //   Report Count (1)
    0x81, 0x01,        //   Input (Const)
    0x26, 0xFF, 0x00,  //   Logical Maximum (255)
    0x46, 0xFF, 0x00,  //   Physical Maximum (255)
    0x09, 0x30,        //   Usage (X)
    0x09, 0x31,        //   Usage (Y)
    0x09, 0x32,        //   Usage (Z)
    0x09, 0x35,        //   Usage (Rz)
    0x75, 0x08,        //   Report Size (8)
    0x95, 0x04,        //   Report Count (4)
    0x81, 0x02,        //   Input (Data,Var,Abs)
    0x06, 0x00, 0xFF,  //   Usage Page (Vendor 0xFF00)
    0x09, 0x20,        //   Usage (0x20)
    0x95, 0x01,        //   Report Count (1)
    0x81, 0x02,        //   Input (Data,Var,Abs)
    0x0A, 0x21, 0x26,  //   Usage (0x2621)
    0x95, 0x08,        //   Report Count (8)
    0x91, 0x02,        //   Output (Data,Var,Abs)
    0xC0,              // End Collection
};

_Static_assert(sizeof(s_report_desc) == 86, "report descriptor length is part of the identity");

static const tusb_desc_device_t s_device_desc = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200,
    .bDeviceClass = 0x00,
    .bDeviceSubClass = 0x00,
    .bDeviceProtocol = 0x00,
    .bMaxPacketSize0 = 64,
    .idVendor = 0x0F0D,
    .idProduct = 0x0092,
    .bcdDevice = 0x0100,
    .iManufacturer = 0x01,
    .iProduct = 0x02,
    .iSerialNumber = 0x00,
    .bNumConfigurations = 0x01,
};

_Static_assert(CFG_TUD_ENDPOINT0_SIZE == 64, "bMaxPacketSize0 above must match the stack");

#define CONFIG_TOTAL_LEN (9 + 9 + 9 + 7 + 7)

static const uint8_t s_config_desc[] = {
    // Configuration: one interface, bus powered, 500 mA
    0x09, TUSB_DESC_CONFIGURATION, U16_TO_U8S_LE(CONFIG_TOTAL_LEN), 0x01, 0x01, 0x00, 0x80, 0xFA,
    // Interface 0: HID, no boot subclass or protocol, two endpoints
    0x09, TUSB_DESC_INTERFACE, 0x00, 0x00, 0x02, TUSB_CLASS_HID, 0x00, 0x00, 0x00,
    // HID 1.11, one report descriptor
    0x09, HID_DESC_TYPE_HID, U16_TO_U8S_LE(0x0111), 0x00, 0x01, HID_DESC_TYPE_REPORT,
    U16_TO_U8S_LE(sizeof(s_report_desc)),
    // Endpoint 1 IN, interrupt
    0x07, TUSB_DESC_ENDPOINT, EP_IN, TUSB_XFER_INTERRUPT, U16_TO_U8S_LE(EP_SIZE), EP_INTERVAL_MS,
    // Endpoint 2 OUT, interrupt
    0x07, TUSB_DESC_ENDPOINT, EP_OUT, TUSB_XFER_INTERRUPT, U16_TO_U8S_LE(EP_SIZE), EP_INTERVAL_MS,
};

_Static_assert(sizeof(s_config_desc) == CONFIG_TOTAL_LEN, "wTotalLength must match the descriptor");

static const char s_lang_id[] = {0x09, 0x04};
static const char *s_strings[] = {s_lang_id, "HORI CO.,LTD.", "POKKEN CONTROLLER"};

// TinyUSB frees the IN endpoint before it runs the completion callback, and
// the callback's buffer is the one the next send writes into. So this file
// keeps its own copy of the report in flight and refuses a new one until the
// callback has taken that copy.
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static bool s_in_flight;
static pad_state_t s_in_flight_state;
static pad_state_t s_last_sent = PAD_STATE_NEUTRAL;
static void (*s_delivered_cb)(const pad_state_t *delivered);

static void encode_report(const pad_state_t *s, uint8_t out[REPORT_LEN])
{
    out[0] = (uint8_t)(s->buttons & 0xFF);
    out[1] = (uint8_t)(s->buttons >> 8);
    out[2] = s->hat;
    out[3] = s->lx;
    out[4] = s->ly;
    out[5] = s->rx;
    out[6] = s->ry;
    out[7] = 0;
}

static void clear_in_flight(void)
{
    portENTER_CRITICAL(&s_lock);
    s_in_flight = false;
    portEXIT_CRITICAL(&s_lock);
}

// A bus reset or an unplug cancels the transfer in flight without a
// completion callback; both end in one of these two events.
static void on_usb_event(tinyusb_event_t *event, void *arg)
{
    (void)arg;
    if (event->id == TINYUSB_EVENT_ATTACHED || event->id == TINYUSB_EVENT_DETACHED) {
        clear_in_flight();
    }
}

esp_err_t hid_backend_init(void)
{
    tinyusb_config_t cfg = TINYUSB_DEFAULT_CONFIG(on_usb_event);
    cfg.descriptor.device = &s_device_desc;
    cfg.descriptor.full_speed_config = s_config_desc;
    cfg.descriptor.string = s_strings;
    cfg.descriptor.string_count = sizeof(s_strings) / sizeof(s_strings[0]);
    return tinyusb_driver_install(&cfg);
}

bool hid_backend_ready(void)
{
    portENTER_CRITICAL(&s_lock);
    bool in_flight = s_in_flight;
    portEXIT_CRITICAL(&s_lock);
    return !in_flight && tud_hid_ready();
}

bool hid_backend_send(const pad_state_t *s)
{
    if (!tud_hid_ready()) {
        return false;
    }
    portENTER_CRITICAL(&s_lock);
    bool taken = !s_in_flight;
    if (taken) {
        s_in_flight = true;
        s_in_flight_state = *s;
    }
    portEXIT_CRITICAL(&s_lock);
    if (!taken) {
        return false;
    }

    uint8_t report[REPORT_LEN];
    encode_report(s, report);
    if (!tud_hid_report(0, report, sizeof(report))) {
        clear_in_flight();
        return false;
    }
    portENTER_CRITICAL(&s_lock);
    s_last_sent = *s;
    portEXIT_CRITICAL(&s_lock);
    return true;
}

uint8_t hid_backend_id(void)
{
    return POKKEN_BACKEND_ID;
}

void hid_backend_set_delivered_cb(void (*cb)(const pad_state_t *delivered))
{
    s_delivered_cb = cb;
}

uint8_t const *tud_hid_descriptor_report_cb(uint8_t instance)
{
    (void)instance;
    return s_report_desc;
}

uint16_t tud_hid_get_report_cb(uint8_t instance, uint8_t report_id, hid_report_type_t report_type,
                               uint8_t *buffer, uint16_t reqlen)
{
    (void)instance;
    (void)report_id;
    if (report_type != HID_REPORT_TYPE_INPUT || reqlen < REPORT_LEN) {
        return 0;
    }
    portENTER_CRITICAL(&s_lock);
    pad_state_t last = s_last_sent;
    portEXIT_CRITICAL(&s_lock);
    encode_report(&last, buffer);
    return REPORT_LEN;
}

void tud_hid_set_report_cb(uint8_t instance, uint8_t report_id, hid_report_type_t report_type,
                           uint8_t const *buffer, uint16_t bufsize)
{
    (void)instance;
    (void)report_id;
    (void)report_type;
    (void)buffer;
    (void)bufsize;
}

void tud_hid_report_complete_cb(uint8_t instance, uint8_t const *report, uint16_t len)
{
    (void)instance;
    (void)report;
    portENTER_CRITICAL(&s_lock);
    bool was_in_flight = s_in_flight;
    pad_state_t delivered = s_in_flight_state;
    s_in_flight = false;
    portEXIT_CRITICAL(&s_lock);
    if (was_in_flight && len == REPORT_LEN && s_delivered_cb != NULL) {
        s_delivered_cb(&delivered);
    }
}

void tud_hid_report_failed_cb(uint8_t instance, hid_report_type_t report_type, uint8_t const *report,
                              uint16_t xferred_bytes)
{
    (void)instance;
    (void)report;
    (void)xferred_bytes;
    if (report_type == HID_REPORT_TYPE_INPUT) {
        clear_in_flight();
    }
}

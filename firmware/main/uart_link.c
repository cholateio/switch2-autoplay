#include "uart_link.h"

#include <string.h>

#include "driver/uart.h"
#include "executor.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "hid_backend.h"

// UART0 on its default pins, which the board wires to its USB-serial bridge.
// The console is disabled in sdkconfig.defaults, so nothing else writes here.
#define LINK_UART UART_NUM_0
#define LINK_TX_PIN 43
#define LINK_RX_PIN 44
#define LINK_BAUD 921600
#define RX_BUFFER 1024
#define TX_BUFFER 512
#define TASK_STACK 4096
#define TASK_PRIORITY 9

#define PROTO_VERSION 1
#define SET_STATE_LEN 9
#define HAT_MAX 8

static SemaphoreHandle_t s_tx_mutex;

void uart_link_send(const proto_msg_t *msg)
{
    uint8_t frame[PROTO_MAX_FRAME];
    size_t n = proto_encode(msg, frame);
    if (n == 0) {
        return;
    }
    xSemaphoreTake(s_tx_mutex, portMAX_DELAY);
    uart_write_bytes(LINK_UART, frame, n);
    xSemaphoreGive(s_tx_mutex);
}

static void reply(const proto_msg_t *request, uint8_t type)
{
    proto_msg_t msg = {.type = type, .seq = request->seq};
    uart_link_send(&msg);
}

static void reply_nak(const proto_msg_t *request, uint8_t reason)
{
    proto_msg_t msg = {.type = PROTO_NAK, .seq = request->seq, .len = 1, .payload = {reason}};
    uart_link_send(&msg);
}

static void put_u16(uint8_t *out, uint16_t value)
{
    out[0] = (uint8_t)(value & 0xFF);
    out[1] = (uint8_t)(value >> 8);
}

static void reply_pong(const proto_msg_t *request)
{
    executor_status_t status;
    executor_status(true, &status);
    proto_msg_t msg = {
        .type = PROTO_PONG,
        .seq = request->seq,
        .len = 2,
        .payload = {status.flags, status.queue_free},
    };
    uart_link_send(&msg);
}

static void reply_info(const proto_msg_t *request, uint16_t rx_crc_errors)
{
    executor_status_t status;
    executor_status(false, &status);
    uint32_t uptime_ms = executor_now_ms();
    proto_msg_t msg = {.type = PROTO_INFO, .seq = request->seq, .len = 12};
    msg.payload[0] = PROTO_VERSION;
    msg.payload[1] = hid_backend_id();
    msg.payload[2] = status.flags;
    msg.payload[3] = status.queue_free;
    put_u16(&msg.payload[4], (uint16_t)(uptime_ms & 0xFFFF));
    put_u16(&msg.payload[6], (uint16_t)(uptime_ms >> 16));
    put_u16(&msg.payload[8], rx_crc_errors);
    put_u16(&msg.payload[10], status.wd_trips);
    uart_link_send(&msg);
}

static void handle_set_state(const proto_msg_t *m)
{
    if (m->len != SET_STATE_LEN) {
        reply_nak(m, PROTO_NAK_LENGTH);
        return;
    }
    const uint8_t *p = m->payload;
    pad_state_t state = {
        .buttons = (uint16_t)(p[0] | (p[1] << 8)),
        .hat = p[2],
        .lx = p[3],
        .ly = p[4],
        .rx = p[5],
        .ry = p[6],
    };
    uint16_t hold_ms = (uint16_t)(p[7] | (p[8] << 8));
    if (state.hat > HAT_MAX) {
        reply_nak(m, PROTO_NAK_VALUE);
        return;
    }
    switch (executor_push(m->seq, &state, hold_ms)) {
    case PADQ_OK:
    case PADQ_DUP_PENDING:
        reply(m, PROTO_ACK);
        break;
    case PADQ_DUP_DONE:
        reply(m, PROTO_DONE);
        break;
    case PADQ_DUP_DROPPED:
        reply_nak(m, PROTO_NAK_DROPPED);
        break;
    case PADQ_BUSY:
        reply(m, PROTO_BUSY);
        break;
    }
}

static void handle(const proto_msg_t *m, uint16_t rx_crc_errors)
{
    // Every frame with a correct CRC counts as a sign of life, including one
    // that is about to be refused.
    executor_feed_watchdog();

    if (m->type == PROTO_SET_STATE) {
        handle_set_state(m);
        return;
    }
    if (m->type != PROTO_PING && m->type != PROTO_RELEASE_ALL && m->type != PROTO_HELLO) {
        reply_nak(m, PROTO_NAK_TYPE);
        return;
    }
    if (m->len != 0) {
        reply_nak(m, PROTO_NAK_LENGTH);
        return;
    }
    switch (m->type) {
    case PROTO_PING:
        reply_pong(m);
        break;
    case PROTO_RELEASE_ALL:
        executor_release_all();
        reply(m, PROTO_ACK);
        break;
    default:
        executor_reset_session();
        reply_info(m, rx_crc_errors);
        break;
    }
}

static void rx_task(void *arg)
{
    (void)arg;
    proto_parser_t parser;
    proto_parser_init(&parser);
    uint8_t buf[128];
    for (;;) {
        // Block for the first byte, then take whatever else has arrived.
        int n = uart_read_bytes(LINK_UART, buf, 1, portMAX_DELAY);
        if (n <= 0) {
            continue;
        }
        size_t waiting = 0;
        uart_get_buffered_data_len(LINK_UART, &waiting);
        if (waiting > sizeof(buf) - 1) {
            waiting = sizeof(buf) - 1;
        }
        if (waiting > 0) {
            int more = uart_read_bytes(LINK_UART, buf + 1, waiting, 0);
            if (more > 0) {
                n += more;
            }
        }
        for (int i = 0; i < n; i++) {
            proto_parser_feed(&parser, buf[i]);
            proto_msg_t msg;
            while (proto_parser_next(&parser, &msg)) {
                handle(&msg, parser.crc_errors);
            }
        }
    }
}

void uart_link_start(void)
{
    s_tx_mutex = xSemaphoreCreateMutex();
    configASSERT(s_tx_mutex != NULL);

    const uart_config_t config = {
        .baud_rate = LINK_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    ESP_ERROR_CHECK(uart_driver_install(LINK_UART, RX_BUFFER, TX_BUFFER, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_param_config(LINK_UART, &config));
    ESP_ERROR_CHECK(uart_set_pin(LINK_UART, LINK_TX_PIN, LINK_RX_PIN, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));

    BaseType_t ok = xTaskCreatePinnedToCore(rx_task, "uart_link", TASK_STACK, NULL, TASK_PRIORITY, NULL, 0);
    configASSERT(ok == pdPASS);
}

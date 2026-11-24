#pragma once

// The serial side of docs/protocol.md: receives frames from the PC, answers
// them and hands commands to the executor.

#include "proto.h"

// Call once, after executor_start().
void uart_link_start(void);

// Sends one frame; safe from any task.
void uart_link_send(const proto_msg_t *msg);

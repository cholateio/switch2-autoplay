# Serial protocol

The PC (host) tells the ESP32-S3 (device) which gamepad state to show and for how long. This file is the contract: `src/autoplay/core/controller/protocol.py` and `firmware/components/padcore/` both implement it, and a conformance test checks that they decode the same bytes the same way.

Protocol version: **1**.

## Link

UART, 921600 baud, 8 data bits, no parity, 1 stop bit, no flow control. Multi-byte fields are little-endian.

The host must expect garbage at the start: opening the port can reset the board, and the ROM bootloader prints text at reset.

## Frame

```
0xA5 | len | type | seq | payload[len] | crc8
```

| Field | Size | Meaning |
|---|---|---|
| magic | 1 | always `0xA5` |
| `len` | 1 | payload length, 0 to 16 |
| `type` | 1 | message type (tables below) |
| `seq` | 1 | chosen by the host; a reply carries the `seq` of the command it answers |
| payload | `len` | depends on `type` |
| `crc8` | 1 | CRC-8 over `len`, `type`, `seq` and the payload |

CRC-8: polynomial `0x07`, initial value `0x00`, no reflection, no final XOR. The check value for the ASCII string `123456789` is `0xF4`.

The longest frame is 21 bytes.

Examples:

| Frame | Bytes |
|---|---|
| PING, seq 1 | `a5 00 01 01 12` |
| SET_STATE, seq 2: A pressed, sticks centred, hold 80 ms | `a5 09 02 02 04 00 08 80 80 80 80 50 00 11` |
| ACK, seq 2 | `a5 00 81 02 ad` |
| DONE, seq 2 | `a5 00 82 02 92` |
| PONG, seq 1, flags 1, queue_free 8 | `a5 02 84 01 01 08 eb` |

## Resynchronisation

A receiver keeps the bytes it has not decided on yet (never more than 21) and repeats:

1. Discard bytes until the first one is `0xA5`.
2. If `len` is above 16, this is not a frame: discard that one `0xA5` and go to 1.
3. Wait until `len + 5` bytes are present.
4. If the CRC matches, deliver the frame and remove its bytes. Otherwise discard that one `0xA5` and go to 1.

Only the single magic byte is discarded in steps 2 and 4. Every other byte is scanned again, so a real frame that begins inside the bytes a corrupted frame claimed is still found.

Each discard in step 2 or 4 increments the receiver's error counter (`rx_crc_errors` on the device). A frame that fails is never answered.

A truncated frame holds back the frames after it until `len + 5` bytes have arrived. The host's retransmissions and heartbeat supply those bytes.

## Host to device

| type | Name | Payload | Immediate reply |
|---|---|---|---|
| `0x01` | PING | none | PONG |
| `0x02` | SET_STATE | 9 bytes, below | ACK, BUSY or NAK; DONE follows an ACK later |
| `0x03` | RELEASE_ALL | none | ACK |
| `0x04` | HELLO | none | INFO |

SET_STATE payload:

| Offset | Field | Type | Meaning |
|---|---|---|---|
| 0 | `buttons` | u16 | button bits, below |
| 2 | `hat` | u8 | D-pad: 0 = up, then clockwise in 45° steps to 7 = up-left; 8 = neutral |
| 3 | `lx` | u8 | left stick x, 0 to 255, 128 centred |
| 4 | `ly` | u8 | left stick y, 0 = up |
| 5 | `rx` | u8 | right stick x |
| 6 | `ry` | u8 | right stick y, 0 = up |
| 7 | `hold_ms` | u16 | how long to keep this state before taking the next one |

Button bits: Y `0x0001`, B `0x0002`, A `0x0004`, X `0x0008`, L `0x0010`, R `0x0020`, ZL `0x0040`, ZR `0x0080`, MINUS `0x0100`, PLUS `0x0200`, LS `0x0400`, RS `0x0800`, HOME `0x1000`, CAPTURE `0x2000`.

- **PING** is the heartbeat. It changes nothing.
- **SET_STATE** appends one state to the device's queue.
- **RELEASE_ALL** empties the queue and makes the state neutral at once (no buttons, hat 8, sticks 128).
  "At once" means the next report the device hands to USB is neutral. A report already waiting for the console's poll cannot be recalled, so the console may read one more report with the old state before the neutral one. The same holds for the watchdog.
- **HELLO** starts a session: it does what RELEASE_ALL does and also forgets every remembered `seq`.

## Device to host

| type | Name | Payload |
|---|---|---|
| `0x81` | ACK | none |
| `0x82` | DONE | none; the hold time of the SET_STATE with this `seq` has ended |
| `0x83` | BUSY | none; the queue is full and the command was not taken |
| `0x84` | PONG | `flags` u8, `queue_free` u8 |
| `0x85` | INFO | 12 bytes, below |
| `0x86` | NAK | `reason` u8 |

`flags`: bit 0 = the USB host has configured the gamepad; bit 1 = the watchdog has tripped since the previous PONG. Sending a PONG clears bit 1; INFO reports the same bits without clearing.

`queue_free`: how many more SET_STATE commands the device can take right now.

INFO payload:

| Offset | Field | Type | Meaning |
|---|---|---|---|
| 0 | `proto_ver` | u8 | 1 |
| 1 | `backend` | u8 | gamepad identity: 1 = HORI Pokken pad |
| 2 | `flags` | u8 | as in PONG |
| 3 | `queue_free` | u8 | as in PONG |
| 4 | `uptime_ms` | u32 | time since the device booted |
| 8 | `rx_crc_errors` | u16 | discarded frame starts since boot; stops at 65535 |
| 10 | `wd_trips` | u16 | watchdog trips since boot; stops at 65535 |

NAK reasons:

| reason | Meaning |
|---|---|
| 1 | the payload length is wrong for this type |
| 2 | unknown type |
| 3 | a value is invalid (`hat` above 8) |
| 4 | this SET_STATE was dropped by the watchdog or by RELEASE_ALL and was not completed |

## Queue and timing

- The device holds at most **8** SET_STATE commands that are not finished, counting the one being shown. A ninth gets BUSY.
- States are shown in the order they were accepted.
- **The hold time starts when the USB host has taken the first report that carries the state**, not when the state leaves the queue. From that moment the state stays for at least `max(hold_ms, 16)` ms. Then the device sends DONE and takes the next state.
- When the queue is empty **the last state stays** until another command or the watchdog changes it.
- If the gamepad is not configured by a USB host, or the host stops polling, the state is never delivered: its hold time does not start and no DONE is sent. The host finds out by its own timeout and from `flags` bit 0.

## Duplicates and lost replies

The device remembers the last **16** SET_STATE `seq` values it accepted, each with a status:

| Status | Meaning | Reply when the same `seq` arrives again |
|---|---|---|
| pending | queued or being shown | ACK |
| done | hold time ended, DONE was sent | DONE |
| dropped | removed by the watchdog or RELEASE_ALL | NAK reason 4 |

A SET_STATE whose `seq` is remembered is never queued a second time, whatever its payload. This makes retransmission safe:

- No ACK within 50 ms: the host sends the same frame again, up to 3 retransmissions.
- No DONE when it was due: the host sends the same frame again. DONE means it finished; ACK means keep waiting; NAK reason 4 means it will never finish.

The host sends one command at a time and waits for its immediate reply before sending the next.

A `seq` must not be reused for a different SET_STATE while the device may still remember it, that is, within 16 SET_STATE commands. Counting every command on one counter does **not** guarantee this: 255 PINGs between two SET_STATEs bring the same `seq` round again, and the device would answer DONE without showing the state. The Python host therefore numbers SET_STATE commands 0 to 127 in turn and every other command 128 to 255 in turn. The two ranges also keep a late reply to one kind of command from being taken for the reply to the other. HELLO clears the device's memory.

RELEASE_ALL and HELLO may be retransmitted freely: repeating them changes nothing further.

## Watchdog

If **300 ms** pass without a frame that has a correct CRC, the device empties the queue, makes the state neutral and counts one trip in `wd_trips`. Every unfinished SET_STATE becomes *dropped*, and no DONE is sent for it.

- Any frame with a correct CRC feeds the watchdog, including one that is answered with NAK.
- The watchdog starts with the first such frame after boot. Before that the state is neutral and there is nothing to release.
- It trips once per silence. After a trip it waits for the next correct frame before it can trip again.

A long hold therefore needs a heartbeat: the host sends PING at least every 100 ms while it wants the state kept. The host stops sending PING when its own control loop stops making progress, so a hung or killed program releases the gamepad within the watchdog time.

## Device guarantees

These two values are fixed in the firmware (`PADQ_MIN_HOLD_MS`, `PADQ_WATCHDOG_MS`). The host's own timing values are in `src/autoplay/config/timing.yaml`.

| Guarantee | Value |
|---|---|
| Shortest time any state is shown once the host has taken it | 16 ms |
| Silence after which the device releases everything | 300 ms |

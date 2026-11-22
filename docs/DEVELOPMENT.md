# Development

Everything you need to pick this project back up after months away.

## Restart in 30 seconds

```bash
uv run pytest                                  # WSL: sync the Linux venv (.venv/) and run the pure-logic tests
scripts/win pytest tests/test_cli.py -v        # Windows-native Python, same checkout
scripts/win ap --version                       # the CLI under Windows
```

## Two environments

There is one checkout, inside WSL. Both environments use it directly.

| | WSL (Ubuntu 24.04) | Windows 11 (native) |
|---|---|---|
| Used for | pure-logic tests, editing, git | anything touching hardware: capture card, ESP32 serial port |
| Python | system Python 3.12 via uv | uv-managed CPython 3.12 |
| venv | `.venv/` in the repo (gitignored) | `%LOCALAPPDATA%\switch2-autoplay\venv` |
| Run | `uv run <cmd>` | `scripts/win <cmd>` |

`scripts/win <args…>` runs `uv.exe run --project <repo UNC path> --directory <repo UNC path> <args…>`:

- The working directory is the repo root (as `\\wsl.localhost\…`), so relative paths in arguments are relative to the repo root, and `fixtures/` and `runs/` written by Windows Python land in this same WSL checkout. There is no mirror copy.
- The venv is kept off the UNC share: the script resolves `%LOCALAPPDATA%` through `cmd.exe` and sets `UV_PROJECT_ENVIRONMENT`, appending it to `WSLENV` so it reaches `uv.exe`.
- It finds `uv.exe` on `PATH` first, otherwise at `%USERPROFILE%\.local\bin\uv.exe`.
- Before running, it creates the venv if it is missing (`uv venv`, which does not start the launcher) and replaces `venv\Scripts\python.exe` with a copy of the uv-managed interpreter (plus its DLLs) whenever the two differ. uv creates that file as its own unsigned launcher, and Smart App Control blocked the launcher on 2026-11-17 (`Failed to query Python interpreter … os error 4551`) although it had run on 2026-11-11; the interpreter itself has stayed allowed. A copied interpreter next to `pyvenv.cfg` runs as the venv's python, and uv accepts it.
- Leading `--group NAME` pairs go to `uv run`: `scripts/win --group hw …` installs the `hw` dependency group (hidapi, esptool) before running.
- A first argument of `pytest` or `ap` is rewritten to `python -m pytest` / `python -m autoplay`, because Smart App Control blocks unsigned console-script launchers with os error 4551: `pytest.exe` is blocked today, and `ap.exe` is the same unsigned launcher and can be blocked at any time. Other arguments pass through unchanged.
- `scripts/win --print-env` prints the resolved venv path and runs nothing.

## How uv was installed on Windows

```bash
cd /mnt/c && powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
cd /mnt/c && "$(wslpath -u "$(cmd.exe /c 'echo %USERPROFILE%' | tr -d '\r')")/.local/bin/uv.exe" python install 3.12
```

The installer refuses to run under the default execution policy; `-ExecutionPolicy Bypass` applies to that one process only. It puts `uv.exe` in `%USERPROFILE%\.local\bin`, which is not on `PATH` (the installer does not edit `PATH`); `scripts/win` does not need it to be. The Windows venv is created on the first `scripts/win` run.

## Commands

| Command | Purpose |
|---------|---------|
| `uv run pytest` | Pure-logic test suite (WSL) |
| `uv run ruff check .` | Lint |
| `kit verify` | The fast checks declared in `kit.toml [verify.fast]`: lint, format check, the firmware host tests, and the test suite without `test_pad_selftest.py` and two sequence-number tests that sleep on real timers (about 25 s) |
| `kit verify --all` | The fast checks, then `[verify.slow]`: the tests the fast lane leaves out, the whole suite under Windows-native Python, and a firmware build in `firmware/build-verify/` whose `sdkconfig` is regenerated from `sdkconfig.defaults` on every run (about 100 s) |
| `scripts/win pytest …` | Tests under Windows-native Python |
| `scripts/win ap …` | The `ap` CLI under Windows-native Python |
| `scripts/win ap probe` | List capture devices and measure size, format and frame rate |
| `scripts/win ap probe --loopback --monitor N` | Measure how old captured frames are (PC output wired to the card) |
| `scripts/win ap capture --labels a,b,c` | Preview the capture card and save labelled screenshots to `fixtures/screens/` |
| `scripts/win ap capture --source video:clip.mp4` | Preview a recorded source instead: `video:<path>` or `dir:<folder of images>` |
| `. ~/esp/esp-idf/export.sh && idf.py -C firmware build` | Build the firmware into `firmware/build/` (WSL) |
| `make -C firmware/test_host test` | Host tests of the firmware's pure-C core (protocol parser, pad queue), built with ASan and UBSan |
| `scripts/flash COM4` | Flash `firmware/build/` to the ESP32-S3 |
| `scripts/flash COM4 build-demo` | Flash the demo build (see Firmware) |
| `scripts/win python scripts/hello.py COM4` | Check the serial link: prints the firmware's INFO and the PING round-trip time |
| `scripts/win ap pad --port COM4 press A` | Drive the gamepad by hand: `press`, `hold`, `stick`, `release-all`, `info`; `--port dummy` prints what would be sent |
| `scripts/win ap soak --port COM4 --seq DPAD_RIGHT,DPAD_LEFT --cycles 100` | Press a sequence many times and check on the capture that every press changed the picture and every cycle returned to its start; failures are saved under `runs/` |
| `scripts/win ap template --image shot.png --name STATE --states-file fixtures/m2/states.yaml` | Cut a template out of a screenshot (drag a box, or pass `--roi x,y,w,h`) and add the state to a states file; `--kind black` adds a black-screen state |
| `scripts/win ap classify --states-file fixtures/m2/states.yaml --expect-from-dirname fixtures/m2/screens/*` | Print the state each screenshot gets; with `--expect-from-dirname` the folder name is the right answer and a folder that is not a state means "no state" |
| `scripts/win ap latency --port COM4 --button DPAD_RIGHT --undo DPAD_LEFT --n 30` | Measure the time from a button press to the first changed frame (p50/p90/max) |
| `scripts/win --group hw ap pad --port COM4 selftest` | Check every button, the hold times and the watchdog by reading the reports back over USB (both board ports plugged into the PC) |
| `scripts/win ap play --pack game_packs/pokopia --port COM4 --goal "…"` | Let the model pursue a goal through a game pack; F12 stops it. `--dummy` instead of `--port` records inputs without a gamepad, `--max-usd N` and `--model ID` override `config/agent.yaml`. Exit code: 0 done, 1 error, 3 emergency stop, 4 budget |
| `uv run ap lessons list --pack game_packs/pokopia` | List the lesson drafts runs have written; `promote <slug> --chapter <name>` appends one to a guide chapter, `drop <slug>` deletes one |

## Firmware

The ESP32-S3 firmware lives in `firmware/` and is built in WSL with ESP-IDF, then flashed from Windows, where the serial port is.

**Toolchain (installed once, no sudo):**

```bash
mkdir -p ~/esp && cd ~/esp
git clone -b v6.1.1 --depth 1 --recursive --shallow-submodules https://github.com/espressif/esp-idf.git
cd esp-idf && ./install.sh esp32s3
python3 tools/idf_tools.py install cmake ninja     # instead of the apt packages
```

The compilers land in `~/.espressif`. The apt packages the ESP-IDF guide lists (`flex`, `bison`, `gperf`, `ccache`, `dfu-util`, `libffi-dev`, `libssl-dev`) were not needed. Run `. ~/esp/esp-idf/export.sh` in each new shell before `idf.py`.

**Build and flash:**

```bash
. ~/esp/esp-idf/export.sh
idf.py -C firmware build                 # normal firmware -> firmware/build/
scripts/flash COM4                       # bootloader, partition table and app
```

`scripts/flash` reads the offsets and flash settings from the build's `flasher_args.json` and runs esptool under Windows Python (`scripts/win --group hw python -m esptool`, 460800 baud). esptool is in the `hw` dependency group because Smart App Control blocks the launcher that `uvx esptool` would create. The board resets into the new firmware by itself.

**Demo build** (ignores the serial link and presses D-pad right for 80 ms once per second; used to check that a console accepts the gamepad identity):

```bash
cd firmware
idf.py -B build-demo -D SDKCONFIG=build-demo/sdkconfig -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.demo" build
cd .. && scripts/flash COM4 build-demo
```

It keeps its own `sdkconfig` inside `build-demo/`, so it never changes the normal build's configuration. The demo starts pressing as soon as the board boots: flash the normal firmware again when the check is over.

**Wiring:** the board's UART Type-C port (CH343, `COM4`) goes to the PC and carries flashing and, later, the serial protocol. The OTG Type-C port is the gamepad: it goes to the Switch 2 dock, or to a PC USB port to inspect the HID side.

- `firmware/sdkconfig` is generated from `sdkconfig.defaults` and gitignored; delete it after editing the defaults. `firmware/dependencies.lock` is committed and pins `esp_tinyusb`.
- The console is off (`CONFIG_ESP_CONSOLE_NONE`): the firmware prints nothing, because UART0 is reserved for the binary protocol. The ROM bootloader still prints a few lines at reset.

## Hardware facts

Measured on 2026-11-17 on the owner's machine.

- **Capture card**: AVerMedia GC551 (Live Gamer EXTREME 2), driver 3.2803.64.132. Its DirectShow name is `AVerMedia GC551 Video Capture`, device index 0.
- **It needs a USB 3 link.** On a USB 2.0 port or through a USB 2.0-only cable Windows shows it with error code 10 (`CM_PROB_FAILED_START`). In `Get-PnpDevice` its location path must contain `SS`, not `HS`.
- **Modes**: uncompressed YUY2 only (NV12 and UYVY fail to open), 1920×1080 at 60 fps. `ap probe` measured 60.00 fps, frame interval p50 16.67 ms, p99 17.34 ms.
- **Read it through PyAV, never through OpenCV.** `cv2.VideoCapture(…, CAP_DSHOW)` delivered frames on only about four opens in ten, in streaks; on the other opens no frame ever arrived, and `read()` still returned `True` once per second with an all-black image (`VideoCapture::read` returns `!image.empty()` and ignores the backend's failure). OpenCV's MSMF backend cannot open the card at all. ffmpeg's DirectShow input opened it 14 times out of 14, and `DeviceSource` on PyAV 15 out of 15. OpenCV renders the device's preview pin and this card only has a capture pin; that difference is a suspect, not a proven cause.
- **Frame age** (`ap probe --loopback`, PC HDMI output wired to the card's HDMI IN, that output at 3840×2160 60 Hz): three runs, each 600/600 stamps decoded, p50 64 ms, p99 66–71 ms. This is an upper bound that includes Windows' draw-to-output delay and the card's 4K-to-1080p scaling. The timestamps the DirectShow input attaches to frames sit a constant ~533 ms behind their arrival; that is a timestamp offset, not latency, so `Frame.ts` uses the arrival time.
- **Serial ports**: the ESP32-S3's CH343P port is `COM4` (`USB-Enhanced-SERIAL CH343`, `VID_1A86`). `COM3` (`VID_264A`) is an unrelated device.
- **ESP32-S3 board**: ESP32-S3 rev v0.2, 16 MB flash, 8 MB PSRAM. `esptool --chip esp32s3 --port COM4 --baud 460800` enters the bootloader by itself (no BOOT button) and flashed a 330 KB image in 3.9 s.
- **The Switch 2 accepts the HORI Pokken identity** (gate A, 2026-11-21). With the demo firmware and the OTG port in the dock, the HOME menu showed a connected controller as player 1 and the cursor moved one tile to the right once per second; the capture card saw a burst of motion every 1.00 s. With the normal firmware (neutral reports only) the picture stayed still. No console setting was changed.
- **On a PC the pad enumerates as `HID\VID_0F0D&PID_0092`**, bus-reported name `POKKEN CONTROLLER`, manufacturer `HORI CO.,LTD.`, no serial number. Windows polls it every 4 ms and gets one 8-byte report per poll; the demo's 80 ms press measured 80.0 ms.
- **Serial link** (2026-11-21): UART0 on GPIO43 (TX) / GPIO44 (RX) through the CH343 bridge at 921600 baud. `scripts/hello.py` measured a PING round trip of 0.48 ms (p50) and 0.6 ms (max) over 100 pings, with no damaged frame in either direction.
- **Read the port one byte first.** `port.read(n)` with a timeout waits the whole timeout unless `n` bytes arrive, and Windows rounds that wait up to a 15.6 ms tick: the same round trip measured 15.4 ms that way. `port.read(1)` followed by `port.read(port.in_waiting)` returns as soon as data is there.
- **The first time the port is opened after flashing, the board resets** (INFO showed an uptime of 181 ms); later opens do not reset it. A host must therefore retry HELLO for a while instead of failing on the first unanswered one.
- **A new state reaches the console 11 to 14 ms after its command** (hold 80 ms: DONE after 94 ms; hold 0: DONE after 27 ms; hold 1000 ms: DONE after 1013 ms). The firmware always has one report waiting for the console's next poll, so a new state goes out one poll after the report already waiting.
- **`ap pad selftest` passes on the board** (2026-11-21, three runs, both ports on the PC): all 14 buttons and 8 D-pad directions read back correctly; a 0 ms press was shown 20 to 24 ms and an 80 ms press 84 to 88 ms (Windows reads the pad every 4 ms, so these are multiples of 4); with the heartbeat stopped the stick was released 304 to 307 ms after the last frame.
- **PokemonAutomation's firmware was never checked on the console.** Its `.bin` was flashed and verified during M0, but Smart App Control blocks its unsigned Windows client and the owner chose to leave Smart App Control on. Our own firmware has replaced it on the board.
- **Reference screenshots**: `ap capture` saved 24 full-resolution (1920×1080) screenshots under the gitignored `fixtures/screens/<label>/`, at least three each for `main_menu`, `in_game`, `dialog`, `dialog_choice`, `loading_black`, `move_wheel` and `workbench`. The loading screen is pure black (mean luma 0.0).
- **Switch 2 idle behaviour**: after a few minutes without input the console dims the picture while still sending a signal; press a button before saving screenshots. Turn auto-sleep off for long runs: a sleeping console and a dead capture stream look the same from the capture side.

## Game packs and the agent

A game pack is a directory (`game_packs/<name>/`) with everything the core knows about one game:

| File | What it is |
|------|------------|
| `manifest.yaml` | Name, home context, guide files, states file, and the buttons each context forbids (`"*"` = everywhere) |
| `guide/core.md` | Sent with every model call; kept short |
| `guide/<chapter>.md` | Read by the model on demand (`read_guide`) |
| `states.yaml` | One state per context, in the `ap template` format |
| `templates/` | Template images cut from screenshots. **Gitignored**: rebuild them on a new machine |
| `knowledge/` | Source material the guide was written from |

**Rebuilding the templates.** Capture each context with `scripts/win ap capture`, then for each one run `scripts/win ap template --image <shot> --name <context> --states-file game_packs/<name>/states.yaml` (the template is saved in `templates/` beside the states file) and pick a part of the picture that is fixed in that context (a button-hint bar or a menu frame, not scenery). Check with `ap classify --expect-from-dirname`. A pack whose templates are missing fails to load with a message naming the file.

**The API key.** `ap play` reads `ANTHROPIC_API_KEY` from the environment, else from `.env` in the working directory (gitignored; `.env.example` is the template). The value is never printed or logged.

**Memory** lives in `memory/<pack name>/` (gitignored): `notes.md` (progress notes, at most `notes_limit` characters, replaced whole) and `lessons/<slug>.md` (drafts). The model can write drafts but cannot promote them; `ap lessons promote` refuses a draft that never worked in use unless `--unverified` is given.

**Forbidden buttons.** Every input goes through `InputGuard`: it reads the context from two fresh frames, refuses a button the pack forbids there, and sends the rest. A picture that matches no single context refuses every button forbidden anywhere. What it cannot cover: the few tens of milliseconds between the second frame and the gamepad acting. The loop is one thread and waits for the picture to settle after each input, so the context changes only through its own inputs; a menu the game opens by itself inside that window is not seen.

**Auditing a run.** Every attempt is an `input` event in `runs/<stamp>-play/events.jsonl` with `button`, `context`, `seq` and `outcome` (`sent`, `refused` or `failed`). To count the presses of a button that were sent, over all play runs:

```bash
cat runs/*-play/events.jsonl | python3 -c "
import json, sys
events = [json.loads(line) for line in sys.stdin]
for button in ('PLUS', 'MINUS', 'HOME', 'CAPTURE'):
    sent = [e for e in events if e['kind'] == 'input' and e['button'] == button and e['outcome'] == 'sent']
    print(button, len(sent), sorted({str(e['context']) for e in sent}))
"
```

`model_call` events hold the four token counts, the cost in US dollars, the latency and the stop reason; `run_result` holds how the run ended.

**Cost.** Prices per model are in `core/agent/budget.py`, with the date they were checked. Before each model call the prompt is counted with the token-counting endpoint, and the budget reserves that prompt at the dearest input rate plus a full-length reply; it stops the run (`reason=budget`, exit code 4) when that no longer fits. Server-side fallbacks are not used.

## M1 acceptance (2026-11-21)

Run on the owner's Switch 2 with the owner present; pad in the dock, capture card on device 0.

| Check | Result |
|---|---|
| `ap soak --seq DPAD_RIGHT,DPAD_LEFT --cycles 100` on the HOME menu | `cycles=100 step_failures=0 cycle_failures=0`, 115 s |
| `ap soak --seq A,B --cycles 100 --settle-ms 800` with the cursor on System Settings | `cycles=100 step_failures=0 cycle_failures=0`, 195 s |
| Process killed while it held the left stick | picture stopped 0.42 s after the kill (limit 0.5 s) |
| Process alive but no longer renewing its lease (`--stall`) | movement lasted 2.35 s, then stopped by itself (limit `lease_ms` + 0.5 s = 2.5 s) |
| F12 while it held the left stick | program ended with "emergency stop" (exit code 3); picture stopped 0.17 s later |
| PC-side serial cable pulled while it held the left stick | program ended with "serial port COM4 failed" (exit code 1); picture stopped 0.34 s later |

The four hold checks ran in the game, where a held stick keeps the picture moving; the picture was judged still when the difference between consecutive frames fell back to its idle level.

- **Soak thresholds.** `ap soak` reduces each frame to 320×180 grey and takes the mean absolute difference. On the HOME menu the smallest real change, the cursor moving one icon in the bottom row, measured 0.33 to 0.37; the same picture taken twice measured 0.07 to 0.11. The defaults, 0.2 for both, sit between. Opening and closing System Settings measured 22.5 to 23.0.
- **F12 is read by polling the key state** every 20 ms, not by registering a system hot key, because the hot-key registration reserves F12 for debuggers. It worked while the program ran in a terminal started from WSL.

## M2 acceptance (2026-11-21)

Run on the owner's Switch 2 with the owner present; pad in the dock, capture card on device 0.

| Check | Result |
|---|---|
| `ap latency --button DPAD_RIGHT --undo DPAD_LEFT --n 30` on the HOME menu | `measured=30 missed=0 drifted=0`, p50 99.8 ms, p90 116.4 ms, min 99.1 ms, max 116.9 ms |
| `ap classify` on the regrouped screenshots in `fixtures/m2/screens/` | 16/16 right for `main_menu`, `in_game` and `loading_black` (scores ≥ 0.98); 14/14 pictures of the other seven screens got no state (highest wrong score 0.25) |
| `ap classify` on three HOME frames taken after the templates were made (`fixtures/m2/heldout/`) | 3/3 `main_menu`, score 1.00 |

- **Press to picture is about 100 ms on the HOME menu.** That covers the serial command (about 12 ms until the console takes the report), the console drawing the change, HDMI, and the capture card (at most about 64 ms, from the M0 frame-age check). The samples fall into two groups one console frame (16.7 ms) apart: 19 at 99–100 ms and 11 at 116–117 ms. A game may take longer to react than the HOME menu; measure it there before tuning waits.
- **The templates were placed using the same screenshots `ap classify` checks**, so the 16/16 is not a held-out result; the three frames in `fixtures/m2/heldout/` are.

## Easy to forget

- **The pad becomes player 1.** While it is plugged into the dock, a single-player game listens to it and ignores the Joy-Con. To play by hand, unplug the OTG cable from the dock.
- **The first press after the console wakes only registers the pad.** The HOME menu shows the controller icon and the cursor does not move; presses after that work. Send one throw-away press before an automated run.
- **A console woken from sleep shows a lock screen ("A 繼續"), and the capture card may show its own "No Signal" picture for a few seconds first.** A is safe there: it only leads to the HOME menu. Y opens the game news; avoid it.
- **Never press A on the HOME menu without checking where the cursor is.** The bottom row holds Nintendo Switch Online and the eShop, which this project does not touch; take a screenshot and read the label first.

- **`do_until` needs `heartbeat=pad.heartbeat`.** It can wait for seconds without touching the controller; without the heartbeat a held button is released after `lease_ms` and an emergency stop is not noticed until the next action.
- **A template must have detail.** OpenCV scores a flat-colour template 1.0 everywhere, so `ap template` and the states loader refuse one.
- **`fixtures/screens/` is labelled loosely** (its `main_menu` holds the HOME menu, the title screen, the pause menu and the bag). `fixtures/m2/screens/` holds copies regrouped by what each picture shows; `fixtures/m2/states.yaml` is the states file checked against it. Both are local only.

- Windows Smart App Control is on. Any other venv launcher you call through `scripts/win` (e.g. `numpy-config`) may fail with `Failed to spawn: … (os error 4551)`; call it as `scripts/win python -m <module>` instead. Blocks are logged as Code Integrity event 3077 in `Microsoft-Windows-CodeIntegrity/Operational`.
- `scripts/win` runs from the repo root, not your current directory.
- Python's `write_text` does not create parent directories; `runs/` and `fixtures/` must exist before something writes into them.
- `fixtures/` and `runs/` are gitignored. Never commit game screenshots, recordings or other people's videos; tests use synthetic frames.

## Docs map

| File | What it holds |
|------|---------------|
| `AGENTS.md` | Goal, stack, layout, constraints and conventions (tool-neutral) |
| `docs/specs/` | Requirement specs |
| `docs/research/` | Research notes the specs cite |
| `docs/protocol.md` | The PC-to-ESP32 serial protocol; the firmware and the Python codec both follow it |
| `docs/verify.md` | What a person checks by hand after `kit verify --all` is green |
| `docs/DEVELOPMENT.md` | This file |

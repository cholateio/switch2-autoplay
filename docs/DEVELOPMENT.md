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
| `states.yaml` | One state per context, in the `ap template` format. An entry with `overlay: true` is not a context but something shown on top of one (Pokopia: the cursor of a choice list) |
| `routines.py` (optional) | Fixed sequences the model gets as tools; see below |
| `templates/` | Template images cut from screenshots. **Gitignored**: rebuild them on a new machine |
| `knowledge/` | Source material the guide was written from |

**Rebuilding the templates.** Capture each context with `scripts/win ap capture`, then for each one run `scripts/win ap template --image <shot> --name <context> --states-file game_packs/<name>/states.yaml` (the template is saved in `templates/` beside the states file) and pick a part of the picture that is fixed in that context (a button-hint bar or a menu frame, not scenery). Check with `ap classify --expect-from-dirname`. A pack whose templates are missing fails to load with a message naming the file.

**The API key.** `ap play` reads `ANTHROPIC_API_KEY` from the environment, else from `.env` in the working directory (gitignored; `.env.example` is the template). The value is never printed or logged.

**Memory** lives in `memory/<pack name>/` (gitignored): `notes.md` (progress notes, at most `notes_limit` characters, replaced whole) and `lessons/<slug>.md` (drafts). The model can write drafts but cannot promote them; `ap lessons promote` refuses a draft that never worked in use unless `--unverified` is given.

**Forbidden buttons.** Every input goes through `InputGuard`: it reads the context from two fresh frames, refuses a button the pack forbids there, and sends the rest. A picture that matches no single context refuses every button forbidden anywhere. What it cannot cover: the few tens of milliseconds between the second frame and the gamepad acting. The loop is one thread and waits for the picture to settle after each input, so the context changes only through its own inputs; a menu the game opens by itself inside that window is not seen.

**Auditing a run.** Every attempt is an `input` event in `runs/<stamp>-play/events.jsonl` with `button`, `context`, `seq` and `outcome` (`sent`, `refused` or `failed`); a `combo` also names the button it held in `held`, the buttons that went down or may have in `reached` (whatever the outcome), and the context read while they were held in `context_held`. To count the presses of a button that were sent, over all play runs:

```bash
cat runs/*-play/events.jsonl | python3 -c "
import json, sys
events = [json.loads(line) for line in sys.stdin]
for button in ('PLUS', 'MINUS', 'HOME', 'CAPTURE'):
    sent = [e for e in events if e['kind'] == 'input'
            and (e['outcome'] == 'sent' or button in e.get('reached', []))
            and (e['button'] == button or button in e.get('reached', []))]
    print(button, len(sent), sorted({str(e['context']) for e in sent}))
"
```

`model_call` events hold the four token counts, the cost in US dollars, the latency and the stop reason; `run_result` holds how the run ended.

**Cost.** Prices per model are in `core/agent/budget.py`, with the date they were checked. Before each model call the prompt is counted with the token-counting endpoint, and the budget reserves that prompt at the dearest input rate plus a full-length reply; it stops the run (`reason=budget`, exit code 4) when that no longer fits. Server-side fallbacks are not used.

**Routines.** A pack may name a Python file under `routines:`. Each function marked `@routine(description, **params)` (`autoplay.core.pack.routines`) becomes a model tool of the same name; it gets a `Hands` object (`core/pack/hands.py`) whose inputs all go through the input guard, and returns the text reported to the model, or raises `RoutineStopped(message, step=...)`. The tool result is that text plus one screenshot. `on_stop:` names a routine without arguments that the loop runs once when a run ends (not after an emergency stop), so that a run stopped by its budget does not leave the game half-way. `confirm: {<context or overlay>: [BUTTON]}` makes a button need a `confirm` text there, which is logged; routines must pass one too. A pack's Python is trusted local code: it is imported from the pack directory named on the command line, nothing else.

The Pokopia routines need more templates than the contexts do (prompts, the pick-up note, the confirmation question); `fixtures/pokopia/cut_templates.py` (local) records where each was cut from. `fixtures/pokopia/rt.py PORT "routine arg=value" ...` runs routines on the console without the model.

**What the loop does to keep a run small.** The conversation keeps the newest `keep_screenshots` (4) pictures; once twice as many have piled up, the older ones are replaced by a line of text. That edits the history, and a thinking block is bound to the conversation it was made in, so every request (counting tokens as well as sending; both were refused before) carries `thinking.block_binding.prefix_mismatch_behavior: "drop_block"` and the beta header `thinking-binding-controls-2026-08-01`: the API leaves out the thinking made before the edit. After a failed tool call, the later calls of the same turn that would touch the game are not run. Each call is sent with the reply length the budget reserved (4 times the longest reply so far, at least 1500 tokens, at most `max_tokens`). With `step_screenshot_width` set (off by default), the screenshots of one turn's tool results are scaled down to that width, all but the last: the model chose every call of a turn before seeing any result, so it decides next on the last picture. This is done before the results are first sent, so it edits nothing the model has seen.

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

## M3 acceptance (2026-11-22)

Run on the owner's Switch 2 with the owner present; pad in the dock, capture card on device 0, model `claude-opus-5-5`.

| Check | Result |
|---|---|
| `ap classify` on the Pokopia screenshots the templates were placed with (`fixtures/pokopia/screens/`) | 101/101 |
| `ap classify` on every third screenshot (`fixtures/pokopia/heldout/`) | 45/46 at first; 46/46 after the `placing` template was recut and its threshold raised to 0.97 because of the one miss. The set was therefore tuned against and is no longer independent evidence |
| All 373 captured frames through `StateRegistry.matches` | none matched more than one context |
| `ap play`, goal "open the bag, then close it", 10 runs | 10/10 `reason=done`; each log shows a press in `bag` and ends in `explore`; 5 model calls, about US$0.06 and 16 s per run |
| `ap play`, goal "open the main menu, then close it", 10 runs | 10/10, same checks; about US$0.07 per run |
| `ap play`, goal "talk to the following Pokémon and end the talk" | done: stick, A, A, down, down, A; 9 model calls, US$0.11 |
| `--max-usd 0.24` on a long goal | stopped after 3 model calls with `reason=budget`, exit code 4, picture in `explore` |
| F12 during a walking goal | `reason=estop`, exit code 3 |
| Audit of all 25 runs | PLUS sent only in `explore`; MINUS, HOME and CAPTURE never sent; nothing refused |

The 25 runs made 127 model calls and cost US$1.79.

- **A in a dialogue is not guarded.** The guard refuses buttons per context and cannot read what a choice says; a choice that gives an item away or sends a Pokémon off is held back only by the guide and the goal. A confirmation rule for choices is still to be designed.
- **`dialog_choice` is not a context.** The choice box moves with the number of choices, so a dialogue with choices reports `dialog` and the model reads the choices from the picture.
- **`placing` means "transformed for Magnet Rise"**: the move label without the two arrows beside it. The same label with arrows is `explore` with that move selected, and scores 0.94 against the template; hence the 0.97 threshold.
- **The first run's presses never reached the console.** The pad had just been plugged back into the dock; one press by hand fixed it (see "Easy to forget"). The model noticed, stopped and said so.
- **The budget stopped early.** `max_tokens` has since been lowered to 3000 (a reserve of about US$0.06 per call). During the acceptance each call reserved a full-length reply (`max_tokens` 8000, about US$0.18), so a run ends when the money left falls below that: with `--max-usd 0.5` after about US$0.3. `--max-usd 0.05` stops before the first call.
- **`ap capture` did not react to its keys** on the owner's machine (digits, `s`); the cause is not known. The screenshots were taken by a timer script instead.

## M4 acceptance (2026-11-23)

Run by the model on the console, at night in the game, with `ap play` and the Pokopia routines. The field was checked after each run: 草原的草皮 back to 99, nothing left standing.

| Check | Result |
| --- | --- |
| A row of 5 blocks placed, each confirmed on the picture, then removed | done in 21 model calls, US$0.685; 5 breaks, each after a passed check. Again on the final code, after the review fixes: 9 calls, US$0.268, bag 99 before and after |
| A 3×2 wall built and taken down | done in 7 model calls, US$0.181 (2026-11-22, input by input: 70 calls, US$1.81) |
| No `Y` without a passed check | 12 `Y` in 6 runs, all inside a routine after its check passed; PLUS, MINUS, HOME, CAPTURE never sent |
| A run stopped by its budget while transformed | ended in `explore`, human form (`on_stop`: "human form again") |
| `A` on a choice list | refused without `confirm`, sent with `confirm: "沒什麼"` |
| 8 model runs in all | 73 calls, US$1.85; three ended in an API error (two before the thinking setting was right, one on a picture inside an error result) |

What was measured on the way, and is in the routines as constants:

- **A placed block or not.** Share of strongly changed pixels in the region in front of the character (mid view), measured once the picture is still: placed 0.047 to 0.36 in about 40 placements, a press that placed nothing 0.001 to 0.025. Threshold 0.04. **Below it the routine stops; it does not press again.** It did at first, and once a block that had been placed read 0.028 (measured while the camera was still following a step), the second press put another block on top and the count was off by one. The cross-model review had named exactly this.
- **Prompts are matched on their white lettering only**, anywhere in the prompt row: the same prompt 0.91 to 1.0, a different one at most 0.67, in night frames and in 21 daytime frames from 2026-11-22. Threshold 0.85.
- **The pick-up note is matched by its "×" only.** The count goes up when breaks follow each other (×2, ×3) and the colour behind the note changes; matching the whole note failed on both.
- **Changes after the review** (2026-11-23): no second press when a placement is not seen; a button that any context forbids or wants confirmed cannot be the held button of a combo; the eat prompt is checked before every berry (with the follower beside the character the game offers "A 看看這個！" instead, and A would hand the berry over); the budget says its bound rests on the counted prompt size and logs `budget_overrun`; a closed output stream is replaced by a sink.
- **The picture alone misses placed blocks** when the character is not in the middle of the screen (after steps to the west the camera does not follow): such blocks read 0.005 to 0.028. So the picture no longer decides. After every press the routine opens the bag, checks that the cursor is on the same cell showing the same item, compares the picture of its count with the one from the look before (no digits are read; the same number gives the same pixels), and takes the item again. Count changed: placed. Count unchanged: nothing was placed, and only then is A pressed once more. About 5.5 s per block instead of 3; a 3×2 wall in 50 s, all six right, two of which the picture would have missed.
- **A tool result that reports an error may hold text only**; a stopped routine sent its screenshot along and the API answered 400. It reports text now and the model calls `look`.
- **`require`.** `InputGuard.press(..., require=predicate)` sends only if the predicate holds on the two frames the context was read from and then on the newest frame, fetched and judged immediately before the send. The Y of a break and the A of eating go through it. Three review rounds were about this gap; what cannot be seen is a change during the last predicate call and on the way to the device.
- **Changes after the whole-batch review** (2026-11-23, late): `ground` and `rise` run only in the placing context and every rise step must show on the picture (0.07 to 0.19 of the middle of the screen changed in 11 steps; hovering in place about 0.01; threshold 0.04); a routine whose function signature does not match its declared arguments fails when the pack loads, not when it is called; the budget prices the part of the prompt that the call before read from the cache as a cache read (for 4 minutes, and not after the history was trimmed), which brings a run with 12 000 to 40 000 prompt tokens to 90% of its cap or more in the unit test.
- **`checked`.** A pack may list buttons that a context lets through only together with a picture requirement (`checked: {placing: [Y]}` for Pokopia). The model's own `press` and `combo` carry none, so there Y reaches the game only from `break_front`, `break_column` or `break_wall`. Added after the third whole-batch review pass; covered by unit tests, and run on the console in the evening of 2026-11-23 (see below). `break_front shape=low` is still **not run on the console**.
- **Daylight, checked offline** on the 373 screenshots of the afternoon of 2026-11-22 (16:32 to 16:48, taken while the owner played):
  - Bag page and cursor: read in all 17 bag frames, each right by eye.
  - The choice overlay: fires in 4 frames, each a list of options. One is "travel to another town? 是／否"; the un-transform question does not match there, so a routine would not answer it.
  - Prompts: "quantity 1" in 25 frames, "end the transformation" in 14, "break" in 1, the hand prompt in 1; no score between 0.67 and 0.91, so nothing ambiguous.
  - **The pick-up note failed in daylight and was fixed.** The template held the end of the item name 草原的草皮 as well as the "×": of 23 daytime notes (other items) it found 2. Cut down to the sign alone it finds 23 of 23, none in the 350 frames without a note, and at night 17 of 17 with none in 31.
  - The break gate: the one daytime frame with the break prompt has a half-hidden outline (62 px) and is refused, as it should be.
  - The outline alone fires in 96 daytime frames that have no break prompt; the gate needs both, but with one daytime prompt frame this is the least tested part in daylight.
- **Console, evening of 2026-11-23 (in-game night; the game's clock follows the real one, so no daylight run was possible):** one block placed and taken back with the routines, no model calls.
  - `place_line` on a cell where the preview was red (the edge of the wooden floor) stopped with "two presses placed nothing" and the bag count unchanged.
  - `break_front` refused twice because the outline was hidden: at ground level, straight behind a block, the character covers it in the mid view, and in the build view the outline sits on the ground cell. `rise steps=2` then `break_column count=1` took the block; the recut pick-up note template was found (the routine requires it).
  - A direct `press Y` while transformed was refused: `outcome: refused, needs_check: true`.
  - `leave`, started transformed with the bag open, closed the bag, answered the un-transform question and ended in explore, human form. No press was lost, so its retry branch did not run.
  - Bag afterwards: 草原的草皮 99, as before.
- **Not available in any daytime screenshot, so unchecked in daylight:** the "PP full" note, the un-transform question, the eat prompt, and the picture change of a rise step. Placement no longer depends on the scene (it reads the bag).

## Cost experiments (2026-11-23, evening)

Run on the console at in-game night, each from human form on flat grass, the field restored after each (草原的草皮 99 before and after every run). One run per row, so differences of a call or two mean nothing. US$2.50 in all: US$2.28 in runs, US$0.22 for the offline check.

"Single inputs" means the goal forbade every routine tool: the model worked with `look`, `press`, `hold_stick`, `combo` and the guide only. This is the part that carries over to another game.

| Task | Model, settings | Model calls | US$ | Result |
|---|---|---|---|---|
| Row of 3 blocks, single inputs | Opus 5.5 | 13 | 0.39 | correct |
| same | Sonnet 5.5 | 9 | 0.14 | correct |
| same | Sonnet 5.5, `screenshot_width: 960` | 9 | 0.15 | correct |
| same | Haiku 5.5 | 20 | 0.03 | failed: 2 of 3 placed, used routine tools against the goal, ended on `max_tokens` |
| Wall 3 wide 2 high, single inputs | Sonnet 5.5 | 11 | 0.13 | correct |
| Ring 3 by 3 (8 blocks), single inputs | Sonnet 5.5 | 15 | 0.36 | correct |
| same, `step_screenshot_width: 640`, `keep_screenshots: 12`, open field | Sonnet 5.5 | 15 | 0.22 | correct |
| same settings, started at the cliff edge | Sonnet 5.5 | 38 | 0.78 | ring correct, but see below |
| Wall built and removed with routines | Sonnet 5.5 | 6 | 0.08 | correct (Opus 5.5 that morning: 7 calls, US$0.20) |

What the numbers say:

- **Sonnet 5.5 did every task Opus 5.5 was given, at 36 to 42 % of the cost.** Haiku 5.5 is a fortieth of the price of Opus and could not do the easiest task.
- **Where the money goes: writing to the cache, 72 to 81 % of every run.** That is new tool results, mostly screenshots (about 1,200 tokens each at 1280 wide), plus the 10,400-token prefix once per run, plus whatever trimming makes the API write again. Cache reads are 7 to 15 %, output 9 to 18 %.
- **Trimming old screenshots costs more than it saves in a run this short.** Dropping pictures changes the history, so everything after the first dropped picture is written to the cache again at 1.25 times the input price, to save reads at 0.05 times. With `keep_screenshots: 4` it pays for itself only some 30 calls after each trim (worked out from the prices, not measured). It is still what bounds a long run.
- **A narrower screenshot for every picture saved nothing** (960: US$0.15 against 0.14) and reads small text worse in the offline check.
- **Smaller pictures for the steps inside a turn, with looser trimming, cut the ring from US$0.36 to 0.22** at the same number of calls. Two runs only, and the other one went badly (next point), so it stays off until it has more runs behind it.
- **The run at the cliff edge.** A block went to the wrong place; the model, following a line in the guide, switched the move to 碎岩 and pressed `ZR`, which broke a ground cell and left a hole with the block lying in it. Nothing in the picture checks had a say, because `ZR` in explore was an ordinary button. Whether the smaller step pictures had a part in the misplacement cannot be told from one run. Fixed the same evening: `checked: {explore: [ZR]}` in the Pokopia manifest, and `magnet_on` sends `ZR` only while the move label reads 電磁飄浮; the guide line is gone. Checked on the console: a direct `ZR` is refused (`needs_check`), `magnet_on` still transforms. The hole was filled (pick the block up with `Y`, stand beside the hole facing it, build view, `A`).
- **Single inputs are no longer the expensive way.** On 2026-11-22 a wall by single inputs took 70 calls; now 11. What changed is in the core and the guide, not in routines: several tool calls per turn with a stop at the first failure, and measured recipes for where a block lands. Routines keep two jobs: a picture check before a button that destroys something, and long repetition.

**Offline check of reading a picture** (`fixtures/pokopia/vis_eval.py`, local): 25 questions with known answers on 8 screenshots (item name and counts in the bag, colour and side of the placement preview, prompts, form, dialog text), one pass per setting. Opus 5.5: 25, 24, 24 right at 1280, 960, 640 wide. Sonnet 5.5: 24, 24, 21. Haiku 5.5: 23, 21, 19, with the side of the preview wrong at every width.

The goals, to run the same tasks again: "Place a row of three 草原的草皮 blocks … using only the single-input tools: look, press, hold_stick, combo, read_guide. Do not call any routine tool. Human form only …", then the same with "a wall three blocks wide and two layers high" and "a square ring … 3 by 3 cells with the middle cell left empty"; each ends with reading the count in the bag.

## Easy to forget

- **The pad becomes player 1.** While it is plugged into the dock, a single-player game listens to it and ignores the Joy-Con. To play by hand, unplug the OTG cable from the dock.
- **The first press after the console wakes only registers the pad.** The HOME menu shows the controller icon and the cursor does not move; presses after that work. Send one throw-away press before an automated run.
- **A console woken from sleep shows a lock screen ("A 繼續"), and the capture card may show its own "No Signal" picture for a few seconds first.** A is safe there: it only leads to the HOME menu. Y opens the game news; avoid it.
- **A press right after another program used the gamepad is sometimes lost** (seen on ZR, X and R). Routines wait for the expected picture and press once more.
- **A picture can change before the game takes inputs again**: after transforming, `placing` is recognised about 1.5 s before R works.
- **Walking with the stick leaves the camera at an angle to the grid** (auto-tracking), and then one-cell steps and placements land in unexpected cells. Square it by eye with short right-stick pushes until a row of blocks is level on screen; no routine does this yet.
- **The facing changes when Magnet Rise ends**, and **the follower Pokémon can take the target or block a step.** Both made a break find nothing; see `guide/magnet.md`.
- **Only pushing a stick does not keep the console awake** (it slept within 45 minutes of camera nudges every 8 minutes); a button press every 5 minutes did for 70 minutes.
- **The 蘋野果 are used up.** The owner allowed eating them for PP while testing; over two days the whole stack of 50 went, the last 5 on 2026-11-23. `eat_berries` has no floor on the stack it eats from; it stops when the first cell of page 1 is not 蘋野果, which is now always. Ask the owner what may be eaten before any more.
- **The character dozes off when left alone for a few minutes** (it lies down with a "z"): X and B do nothing then, a push of the left stick wakes it. The keep-awake press of ZL does not prevent it.
- **The pad cannot wake a sleeping console.** Left without input for about an hour the console sleeps (last press 20:59, "No Signal" at 22:01 on 2026-11-22). `ap pad` then fails with "the console is not polling the gamepad over USB", so no button reaches it; the owner has to wake it with the power button or their own controller. The wake screen ("A 繼續") sleeps again after about a minute without a press (woke 22:48:07, "No Signal" by 22:49:30), so the owner should also press A there.
- **Never press A on the HOME menu without checking where the cursor is.** The bottom row holds Nintendo Switch Online and the eShop, which this project does not touch; take a screenshot and read the label first.

- **`do_until` needs `heartbeat=pad.heartbeat`.** It can wait for seconds without touching the controller; without the heartbeat a held button is released after `lease_ms` and an emergency stop is not noticed until the next action.
- **A template must have detail.** OpenCV scores a flat-colour template 1.0 everywhere, so `ap template` and the states loader refuse one.
- **`fixtures/screens/` is labelled loosely** (its `main_menu` holds the HOME menu, the title screen, the pause menu and the bag). `fixtures/m2/screens/` holds copies regrouped by what each picture shows; `fixtures/m2/states.yaml` is the states file checked against it. Both are local only.

- Windows Smart App Control is on. Any other venv launcher you call through `scripts/win` (e.g. `numpy-config`) may fail with `Failed to spawn: … (os error 4551)`; call it as `scripts/win python -m <module>` instead. Blocks are logged as Code Integrity event 3077 in `Microsoft-Windows-CodeIntegrity/Operational`.
- `scripts/win` runs from the repo root, not your current directory.
- Python's `write_text` does not create parent directories; `runs/` and `fixtures/` must exist before something writes into them.
- `fixtures/` and `runs/` are gitignored. Never commit game screenshots, recordings or other people's videos; tests use synthetic frames.
- After the console was off, the game does not resume where it was left: on 2026-11-23 it came back beside the Pokémon Center with a three-screen announcement (context not recognised at night; `A` three times), and a different camera. The game's day and night follow the real clock.
- Straight behind a block at ground level the break outline is hidden (by the character in the mid view, on the ground cell in the build view): `rise` above it and use `break_column`.
- `ZR` in explore uses whichever move is selected, and several of them change the terrain. It is a `checked` button: only `magnet_on` sends it. A new move the model should be able to use needs its own routine with a picture check.
- `rise` judges each step by how much the picture moved (threshold 0.04). Over plain grass at night it measured 0.033 and 0.035 for a step that had worked or not; running the routine again got through each time.
- One 原味沙拉 (bag page 1, row 1, column 9 on 2026-11-23) fills PP completely. 蘋野果 are used up, so `eat_berries` finds nothing to eat.

## Docs map

| File | What it holds |
|------|---------------|
| `AGENTS.md` | Goal, stack, layout, constraints and conventions (tool-neutral) |
| `docs/specs/` | Requirement specs |
| `docs/research/` | Research notes the specs cite |
| `docs/protocol.md` | The PC-to-ESP32 serial protocol; the firmware and the Python codec both follow it |
| `docs/verify.md` | What a person checks by hand after `kit verify --all` is green |
| `docs/DEVELOPMENT.md` | This file |

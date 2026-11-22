# How to verify this project

<!-- For the person accepting the work. The machine-run checks live in kit.toml [verify]
     (run: kit verify --all). This file is what a human does after those are green. -->

## Start it

Without the hardware:

```bash
scripts/win ap --version                    # the CLI under Windows-native Python
scripts/win ap pad --port dummy press A     # prints the gamepad states it would send
```

With the hardware, and only while the owner is watching (console on, the board's OTG port in the dock, its UART port on the PC as `COM4`, capture card on device 0):

```bash
scripts/win python scripts/hello.py COM4            # serial link
scripts/win ap probe                                # capture card
scripts/win ap pad --port COM4 press DPAD_RIGHT     # one press, on the HOME menu
```

- Where it answers: the terminal, and for the gamepad the console's own picture.
- Test account or data: none. `ap play` needs `ANTHROPIC_API_KEY` in `.env` (template: `.env.example`), spends money on every run, and needs a game pack whose `templates/` exist on this machine. Never a real credential in a command line or a log.

## Look at

1. `ap --version` prints `ap <version>`.
2. `ap pad --port dummy press A` prints two lines: a state with `Button.A` and `hold_ms=80`, then a neutral state with `hold_ms=50`.
3. `hello.py COM4` prints the firmware's INFO and a PING round trip below 1 ms.
4. `ap probe` lists `AVerMedia GC551 Video Capture` at 1920×1080, 60 fps.
5. On the HOME menu the cursor moves one tile to the right. The first press after the console wakes only registers the pad; press once more.
6. For a change to the agent loop or a game pack: run `scripts/win ap play --pack game_packs/<name> --port COM4 --goal "…" --max-usd 0.5` and watch it. F12 ends the program with exit code 3 and the picture stops moving.

## It is wrong if

- `kit verify --all` prints `FAIL` or `DIRTY`, or its last line does not start with `KIT_VERIFY green` (a good run ends like `KIT_VERIFY green tree=… fast=4/4 slow=3/3 secs=…`).
- A `scripts/win` command fails with `os error 4551`: Smart App Control blocked a launcher; call the tool as `scripts/win python -m <module>`.
- `hello.py` gets no INFO, or `ap probe` finds no device or a mode other than 1920×1080 at 60 fps.
- The cursor does not move on the second press, or anything on the console keeps moving more than 0.5 s after the program ends.
- A run log (`runs/*-play/events.jsonl`) holds an `input` event with `outcome` `sent` for a button the pack forbids in that context. The query is in `docs/DEVELOPMENT.md`, "Auditing a run".
- `git status` shows a game screenshot, a template or a recording outside the gitignored paths.

## Checks that are not automated yet

- Everything that needs the console: `ap soak`, `ap latency`, `ap classify` on real screenshots, the emergency stop, the watchdog and the lease. Their results are the acceptance tables in `docs/DEVELOPMENT.md`.
- `ap pad selftest`, which needs both board ports plugged into the PC.
- Flashing the board (`scripts/flash`). The slow lane only proves the firmware builds.
- Whether what the model does in the game makes sense, and what a run cost (`model_call` events in the run log).

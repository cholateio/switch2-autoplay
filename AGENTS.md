<!-- Tool-neutral project memory. Never put local paths, costs or personal data here. -->
# switch2-autoplay

## Project goal

A machine that gives a model eyes (a capture card) and hands (an ESP32-S3 posing as a wired gamepad) so it can play the owner's own Nintendo Switch 2 by itself, built as a game-agnostic core plus swappable game packs (the first is Pokémon Pokopia).

## Stack

- Language / framework: Python 3.12 (`requires-python >= 3.11`) managed by uv; numpy, OpenCV (image processing), PyAV (capture-device input), pyserial, PyYAML, anthropic (the model loop); ESP32-S3 firmware in C on ESP-IDF v6.1 with `esp_tinyusb`
- Services: Anthropic Claude API for the agent loop (`ap play`); nothing else
- Test: `uv run pytest`

## File layout

- `src/autoplay/` — the game-agnostic core and the `ap` CLI (`autoplay.cli:main`); tool modules expose `register(sub) -> None`
- `src/autoplay/core/` — one package per layer: `capture` (frame sources), `perception` (ROI scaling, matching, screen states), `controller` (gamepad), `actions` (`do_until`, timing), `runtime` (run log, emergency stop), `pack` (game pack loader, forbidden-button guard), `memory` (progress notes, lesson drafts), `agent` (model loop, tools, budget), `tools` (the `ap` subcommands)
- `game_packs/<name>/` — one game's knowledge: manifest, guide, states file; its `templates/` are gitignored
- `scripts/win` — runs a command under Windows-native Python from WSL (see `docs/DEVELOPMENT.md`)
- `firmware/` — the ESP32-S3 gamepad firmware; `components/padcore/` is pure C with no ESP-IDF headers, `main/hid_backend.h` is the one interface a gamepad identity implements
- `scripts/flash` — flashes a firmware build to the board from WSL
- `docs/specs/` — requirement specs; the authority for what to build
- `docs/research/` — research notes the specs cite
- `tests/` — pure-logic tests that run in WSL; they use only programmatically generated synthetic frames

## Constraints

- The core must contain no game knowledge: no button semantics, templates or coordinates. Button names use only the generic Switch names.
- Automate single-player content only; never modify the console, never read or write game memory.
- Third-party firmware binaries (the PokemonAutomation `.bin` used for the M0 check) are for personal, non-commercial use only and never enter the repo.
- Game screenshots, recordings and other people's videos live only in the gitignored `fixtures/`; the repo is public and must never contain them. Committed tests use only programmatically generated synthetic frames.
- Code comments are English only and state invariants, cross-file coupling or non-obvious reasons; no narrating comments; docstrings only for public API contracts.
- Hardware work runs under Windows-native Python (invoked from WSL through `scripts/win`); pure-logic tests run in WSL.
- Every gamepad state is held for at least 16 ms; a button press defaults to 80 ms and the release gap to 50 ms. These constants live only in `src/autoplay/config/timing.yaml`.
- Firmware watchdog: 300 ms without a CRC-valid packet clears the queue, releases every button and centres the sticks.
- Coordinates and templates use 1920×1080 as the reference resolution and are scaled when the actual resolution differs.
- Capture never goes through compression or the network; a device source keeps only the latest frame.
- Every action is "do, then confirm"; no blind macros.
- Path-type constraints are mirrored in `kit.toml [protected]`; keep the two in sync (one glob per line).

## Conventions

- Conventional commits; run `uv run ruff check .` and `uv run pytest` before committing.
- GitHub Actions: top-level `permissions: contents: read`, raised per job only when needed.
- GitHub Actions: pin every `uses:` to a 40-character commit SHA with a trailing `# vX.Y.Z` comment.

## Where to look

- `docs/specs/` — requirements and design decisions
- `docs/DEVELOPMENT.md` — restart, commands, environment variables, deploy, gotchas

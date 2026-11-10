<!-- Tool-neutral project memory. Never put local paths, costs or personal data here. -->
# switch2-autoplay

## Project goal

A machine that gives a model eyes (a capture card) and hands (an ESP32-S3 posing as a wired gamepad) so it can play the owner's own Nintendo Switch 2 by itself, built as a game-agnostic core plus swappable game packs (the first is Pokémon Pokopia).

## Stack

- Language / framework: Python 3.12 (`requires-python >= 3.11`) managed by uv; numpy, OpenCV, pyserial, PyYAML; ESP32-S3 firmware later
- Services: Anthropic Claude API for the agent loop (later milestones); nothing else
- Test: `uv run pytest`

## File layout

- `src/autoplay/` — the game-agnostic core and the `ap` CLI (`autoplay.cli:main`); tool modules expose `register(sub) -> None`
- `scripts/win` — runs a command under Windows-native Python from WSL (see `docs/DEVELOPMENT.md`)
- `docs/specs/` — requirement specs; the authority for what to build
- `docs/research/` — research notes the specs cite
- `tests/` — pure-logic tests that run in WSL; they use only programmatically generated synthetic frames

## Constraints

- The core must contain no game knowledge: no button semantics, templates or coordinates.
- Automate single-player content only; never modify the console, never read or write game memory.
- Game screenshots, recordings and other people's videos live only in the gitignored `fixtures/`; the repo is public and must never contain them. Committed tests use only programmatically generated synthetic frames.
- Code comments are English only and state invariants, cross-file coupling or non-obvious reasons; no narrating comments; docstrings only for public API contracts.
- Hardware work runs under Windows-native Python (invoked from WSL through `scripts/win`); pure-logic tests run in WSL.
- Every gamepad state is held for at least 16 ms; a button press defaults to 80 ms and the release gap to 50 ms. These constants live only in `src/autoplay/config/timing.yaml`.
- Firmware watchdog: 300 ms.
- Coordinates and templates use 1920×1080 as the reference resolution.
- Capture never goes through compression or the network.
- Every action is "do, then confirm"; no blind macros.
- Path-type constraints are mirrored in `kit.toml [protected]`; keep the two in sync (one glob per line).

## Conventions

- Conventional commits; run `uv run ruff check .` and `uv run pytest` before committing.
- GitHub Actions: top-level `permissions: contents: read`, raised per job only when needed.
- GitHub Actions: pin every `uses:` to a 40-character commit SHA with a trailing `# vX.Y.Z` comment.

## Where to look

- `docs/specs/` — requirements and design decisions
- `docs/DEVELOPMENT.md` — restart, commands, environment variables, deploy, gotchas

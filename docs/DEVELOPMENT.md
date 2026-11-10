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
| `scripts/win pytest …` | Tests under Windows-native Python |
| `scripts/win ap …` | The `ap` CLI under Windows-native Python |

## Easy to forget

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
| `docs/DEVELOPMENT.md` | This file |

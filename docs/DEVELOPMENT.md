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

## Hardware facts

Measured on 2026-11-17 on the owner's machine.

- **Capture card**: AVerMedia GC551 (Live Gamer EXTREME 2), driver 3.2803.64.132. Its DirectShow name is `AVerMedia GC551 Video Capture`, device index 0.
- **It needs a USB 3 link.** On a USB 2.0 port or through a USB 2.0-only cable Windows shows it with error code 10 (`CM_PROB_FAILED_START`). In `Get-PnpDevice` its location path must contain `SS`, not `HS`.
- **Modes**: uncompressed YUY2 only (NV12 and UYVY fail to open), 1920×1080 at 60 fps. `ap probe` measured 60.00 fps, frame interval p50 16.67 ms, p99 17.34 ms.
- **Read it through PyAV, never through OpenCV.** `cv2.VideoCapture(…, CAP_DSHOW)` delivered frames on only about four opens in ten, in streaks; on the other opens no frame ever arrived, and `read()` still returned `True` once per second with an all-black image (`VideoCapture::read` returns `!image.empty()` and ignores the backend's failure). OpenCV's MSMF backend cannot open the card at all. ffmpeg's DirectShow input opened it 14 times out of 14, and `DeviceSource` on PyAV 15 out of 15. OpenCV renders the device's preview pin and this card only has a capture pin; that difference is a suspect, not a proven cause.
- **Frame age** (`ap probe --loopback`, PC HDMI output wired to the card's HDMI IN, that output at 3840×2160 60 Hz): three runs, each 600/600 stamps decoded, p50 64 ms, p99 66–71 ms. This is an upper bound that includes Windows' draw-to-output delay and the card's 4K-to-1080p scaling. The timestamps the DirectShow input attaches to frames sit a constant ~533 ms behind their arrival; that is a timestamp offset, not latency, so `Frame.ts` uses the arrival time.
- **Serial ports**: the ESP32-S3's CH343P port is `COM4` (`USB-Enhanced-SERIAL CH343`, `VID_1A86`). `COM3` (`VID_264A`) is an unrelated device.
- **Switch 2 idle behaviour**: after a few minutes without input the console dims the picture while still sending a signal; press a button before saving screenshots. Turn auto-sleep off for long runs: a sleeping console and a dead capture stream look the same from the capture side.

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

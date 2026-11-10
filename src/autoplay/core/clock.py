import time


def now() -> float:
    """Return the project-wide timestamp in seconds; every `Frame.ts` and any
    timestamp compared with one must come from this function."""
    # Before Python 3.13 the monotonic clock on Windows is GetTickCount64 with
    # 15.6 ms resolution, a whole 60 fps frame; perf_counter() is QPC (~100 ns).
    return time.perf_counter()

import re
from collections import deque
from collections.abc import Callable
from pathlib import Path

from autoplay.core.capture.base import Frame, FrameSource
from autoplay.core.runtime.runlog import RunLog

# How long one wait for a frame may block before the heartbeat is called again.
_POLL_S = 0.1
# Frame times are sums of float frame periods; a span of exactly `stable_ms`
# must not fall short by rounding.
_EPSILON_S = 1e-6

TIMEOUT = "timeout"
SOURCE_ENDED = "source ended"
CAPTURE_STALE = "capture stale"

Predicate = Callable[[Frame], bool]


class ActionFailed(RuntimeError):
    """An action never produced the picture it was meant to. `attempts` counts
    how often the action was done; `dump_dir` holds the saved frames, or is
    None when no run log was given."""

    def __init__(
        self, name: str, reason: str, attempts: int, dump_dir: Path | None = None
    ) -> None:
        super().__init__(
            f"action {name or '(unnamed)'} failed after {attempts} attempt(s): {reason}"
        )
        self.name = name
        self.reason = reason
        self.attempts = attempts
        self.dump_dir = dump_dir


class _Watch:
    """Looks at the frames of one source, in order, for a predicate that
    holds on consecutive frames spanning at least `stable_ms`."""

    def __init__(
        self,
        source: FrameSource,
        predicate: Predicate,
        stable_ms: int,
        heartbeat: Callable[[], None] | None,
        after_seq: int,
        keep_frames: int,
    ) -> None:
        self._source = source
        self._predicate = predicate
        self._stable_s = stable_ms / 1000
        self._heartbeat = heartbeat
        self.seq = after_seq
        self.tail: deque[Frame] = deque(maxlen=max(keep_frames, 0) or None)
        self._keep = keep_frames > 0

    def _beat(self) -> None:
        if self._heartbeat is not None:
            self._heartbeat()

    def run(
        self, timeout: float, first: Frame | None = None
    ) -> tuple[Frame | None, str]:
        """Return (frame, "") when the predicate has held long enough, else
        (None, reason). The time limit counts on frame timestamps from the
        first frame looked at, and never cuts off a span that is still
        holding: that span either completes or breaks within `stable_ms`."""
        started: float | None = None
        span: float | None = None
        frame = first
        while True:
            if frame is None:
                self._beat()
                frame = self._source.wait_next(self.seq, _POLL_S)
                if frame is None:
                    if self._source.ended():
                        return None, SOURCE_ENDED
                    if self._source.stats().stale:
                        return None, CAPTURE_STALE
                    continue
            self.seq = frame.seq
            if self._keep:
                self.tail.append(frame)
            if started is None:
                started = frame.ts
            if self._predicate(frame):
                if span is None:
                    span = frame.ts
                if frame.ts - span >= self._stable_s - _EPSILON_S:
                    return frame, ""
            else:
                span = None
                if frame.ts - started >= timeout - _EPSILON_S:
                    return None, TIMEOUT
            frame = None


def wait_until(
    predicate: Predicate,
    *,
    source: FrameSource,
    timeout: float,
    stable_ms: int = 100,
    heartbeat: Callable[[], None] | None = None,
) -> Frame | None:
    """Watch the source, starting with its current frame, until `predicate`
    holds on consecutive frames spanning at least `stable_ms`, and return the
    last frame of that span. Returns None when `timeout` seconds of frames
    pass without that, the source ends, or the capture goes stale.

    `heartbeat` is called before every wait for a frame; pass the controller's
    `heartbeat` so a held state is kept and a stop is noticed. Whatever it
    raises propagates."""
    watch = _Watch(source, predicate, stable_ms, heartbeat, 0, 0)
    frame, _ = watch.run(timeout, first=source.latest())
    return frame


def do_until(
    action: Callable[[], None],
    predicate: Predicate,
    *,
    source: FrameSource,
    timeout: float = 3.0,
    retries: int = 2,
    stable_ms: int = 100,
    name: str = "",
    runlog: RunLog | None = None,
    keep_frames: int = 5,
    heartbeat: Callable[[], None] | None = None,
) -> Frame:
    """Do `action`, then watch the frames captured after it began until
    `predicate` holds on consecutive frames spanning at least `stable_ms`
    (0: the first frame it holds on), and return the last frame of that span.

    A frame that already existed before the action never counts. If `timeout`
    seconds of frames pass without success the action is done again, up to
    `retries` more times; then `ActionFailed` is raised with reason "timeout".
    When the source ends or the capture goes stale the action is not repeated
    blind: `ActionFailed` is raised at once, with reason "source ended" or
    "capture stale". With a `runlog`, a failure saves the frame from before
    the action and the last `keep_frames` frames, and logs `action_failed`.

    `heartbeat` is called before every wait for a frame; pass the controller's
    `heartbeat` so a held state is kept and a stop is noticed. Exceptions from
    `action`, `predicate` and `heartbeat` propagate, and nothing is retried."""
    if retries < 0:
        raise ValueError("retries must not be negative")
    before = source.latest()
    watch = _Watch(
        source,
        predicate,
        stable_ms,
        heartbeat,
        before.seq if before is not None else 0,
        keep_frames,
    )
    attempts = 0
    reason = TIMEOUT
    while attempts <= retries and reason == TIMEOUT:
        action()
        attempts += 1
        frame, reason = watch.run(timeout)
        if frame is not None:
            return frame
    dump_dir = None
    if runlog is not None:
        tag = re.sub(r"[^A-Za-z0-9_-]", "_", name) or "action"
        saved = []
        if before is not None:
            saved.append(runlog.save_frame(f"{tag}-before", before))
        saved += [
            runlog.save_frame(f"{tag}-last{i}", kept)
            for i, kept in enumerate(watch.tail, start=1)
        ]
        runlog.event(
            "action_failed",
            name=name,
            reason=reason,
            attempts=attempts,
            frames=[path.name for path in saved],
        )
        dump_dir = runlog.dir
    raise ActionFailed(name, reason, attempts, dump_dir)

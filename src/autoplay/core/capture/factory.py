import re
from pathlib import Path

from autoplay.core.capture.base import FrameSource
from autoplay.core.capture.device import DeviceSource
from autoplay.core.capture.offline import ImageDirSource, VideoFileSource

SOURCE_FORMS = "device:<index>, video:<path> or dir:<path>"


def open_source(spec: str, **kwargs) -> FrameSource:
    """Build the frame source a spec names, without starting it: `device:<index>`,
    `video:<path>` or `dir:<path>`. Keyword arguments go to the source's
    constructor. Raises ValueError for any other spec."""
    kind, sep, rest = spec.partition(":")
    if sep and rest:
        if kind == "device" and re.fullmatch(r"\d+", rest):
            return DeviceSource(int(rest), **kwargs)
        if kind == "video":
            return VideoFileSource(Path(rest), **kwargs)
        if kind == "dir":
            return ImageDirSource(Path(rest), **kwargs)
    raise ValueError(f"unsupported source {spec!r}; expected {SOURCE_FORMS}")

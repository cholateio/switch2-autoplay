import json

import cv2
import numpy as np
import pytest

from autoplay.core.capture.base import Frame
from autoplay.core.runtime.runlog import RunLog


def test_run_directory_is_stamped_and_named(tmp_path):
    log = RunLog("soak", root=tmp_path)
    assert log.dir.parent == tmp_path
    assert log.dir.name.endswith("-soak")
    assert len(log.dir.name) == len("20261121-153000-soak")
    assert log.dir.is_dir()
    log.close()


def test_two_runs_in_the_same_second_get_their_own_directories(tmp_path):
    first, second = RunLog("x", root=tmp_path), RunLog("x", root=tmp_path)
    assert first.dir != second.dir
    assert second.dir.is_dir()
    first.close()
    second.close()


def test_event_writes_jsonl_line(tmp_path):
    log = RunLog("t", root=tmp_path)
    log.event("step", button="A", ok=True)
    log.event("done")
    lines = [json.loads(x) for x in (log.dir / "events.jsonl").read_text().splitlines()]
    assert lines[0]["kind"] == "step"
    assert lines[0]["button"] == "A" and lines[0]["ok"] is True
    assert 0 <= lines[0]["t"] <= lines[1]["t"] < 5
    assert lines[1] == {"t": lines[1]["t"], "kind": "done"}
    log.close()


def test_events_are_on_disk_before_close(tmp_path):
    log = RunLog("t", root=tmp_path)
    log.event("step")
    assert (log.dir / "events.jsonl").read_text().count("\n") == 1
    log.close()


def test_event_rejects_reserved_field_names(tmp_path):
    log = RunLog("t", root=tmp_path)
    with pytest.raises(ValueError):
        log.event("step", t=1)
    log.close()


def test_save_frame_writes_png(tmp_path):
    log = RunLog("t", root=tmp_path)
    image = np.zeros((18, 32, 3), np.uint8)
    image[4:8, 10:20] = (10, 200, 30)
    first = log.save_frame("before", Frame(image, 1.0, 7))
    second = log.save_frame("after", Frame(image, 1.1, 8))
    assert (first.name, second.name) == ("0001-before.png", "0002-after.png")
    assert np.array_equal(cv2.imread(str(first)), image)
    log.close()


@pytest.mark.parametrize("bad", ["", "a b", "../x", "a/b"])
def test_names_that_are_not_plain_are_rejected(tmp_path, bad):
    with pytest.raises(ValueError):
        RunLog(bad, root=tmp_path)
    log = RunLog("ok", root=tmp_path)
    with pytest.raises(ValueError):
        log.save_frame(bad, Frame(np.zeros((2, 2, 3), np.uint8), 0.0, 1))
    log.close()

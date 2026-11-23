import base64
import dataclasses

import cv2
import numpy as np
from fake_model import FakeModel, make_session

from autoplay.core.agent.loop import run_agent, thin_step_pictures
from autoplay.core.agent.tools import encode_frame, shrink_picture


def picture(width=1280, height=720) -> dict:
    rng = np.random.default_rng(width)
    return encode_frame(rng.integers(0, 255, (height, width, 3), np.uint8), width, 80)


def width_of(block: dict) -> int:
    raw = base64.standard_b64decode(block["source"]["data"])
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR).shape[1]


def result(*parts) -> dict:
    return {"type": "tool_result", "tool_use_id": "c", "content": list(parts)}


def text(words="t") -> dict:
    return {"type": "text", "text": words}


def test_all_but_the_last_picture_of_a_turn_are_scaled_down():
    error = {**result(text("Not sent")), "is_error": True}
    results = [
        result(text("a"), picture()),
        error,
        result(text("b"), picture()),
        result(text("c"), picture()),
    ]
    last = results[3]["content"][1]
    assert thin_step_pictures(results, 640, 80) == 2
    assert [width_of(r["content"][1]) for r in (results[0], results[2])] == [640, 640]
    assert results[3]["content"][1] is last and width_of(last) == 1280
    assert [r["content"][0]["text"] for r in results] == ["a", "Not sent", "b", "c"]
    assert results[1] == error
    small = results[0]["content"][1]
    assert width_of(small) == 640 and small["source"]["media_type"] == "image/jpeg"
    height = cv2.imdecode(
        np.frombuffer(base64.standard_b64decode(small["source"]["data"]), np.uint8),
        cv2.IMREAD_COLOR,
    ).shape[0]
    assert height == 360


def test_a_single_picture_and_no_picture_are_left_alone():
    one = [result(text(), picture())]
    kept = one[0]["content"][1]
    assert thin_step_pictures(one, 640, 80) == 0 and one[0]["content"][1] is kept
    none = [result(text()), {**result(text()), "is_error": True}]
    assert thin_step_pictures(none, 640, 80) == 0
    # A tool result whose content is plain text has no parts to look through.
    plain = [{"type": "tool_result", "tool_use_id": "c", "content": "words"}]
    assert thin_step_pictures(plain, 640, 80) == 0


def test_a_picture_that_is_not_wider_or_cannot_be_read_is_returned_as_it_is():
    narrow = picture(480, 270)
    assert shrink_picture(narrow, 640, 80) is narrow
    broken = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": "eA=="},
    }
    assert shrink_picture(broken, 640, 80) is broken
    results = [
        result(text(), broken),
        result(text(), narrow),
        result(text(), picture()),
    ]
    assert thin_step_pictures(results, 640, 80) == 0


def widths_sent(model, call_number) -> list[int]:
    return [
        width_of(part)
        for block in model.results(call_number)
        for part in block["content"]
        if part["type"] == "image"
    ]


def turns():
    looks = [("look", {}), ("press", {"button": "X"}), ("look", {})]
    return [looks, [("look", {})], [("finish", {"summary": "ok"})]]


def test_the_loop_sends_one_full_picture_per_turn_when_a_step_width_is_set(tmp_path):
    session = make_session(tmp_path, moves={"X": ("bag",)})
    full = session.source.latest().bgr.shape[1]
    step = full // 2
    session.config = dataclasses.replace(
        session.config, screenshot_width=full, step_screenshot_width=step
    )
    model = FakeModel(turns())
    assert run_agent(session, "goal", model).reason == "done"
    assert widths_sent(model, 2) == [step, step, full]
    assert widths_sent(model, 3) == [full]
    # What was sent once is sent again unchanged.
    assert model.sent[2][:3] == model.sent[1][:3]


def test_without_a_step_width_every_picture_is_full_size(tmp_path):
    session = make_session(tmp_path, moves={"X": ("bag",)})
    full = session.source.latest().bgr.shape[1]
    session.config = dataclasses.replace(
        session.config, screenshot_width=full, step_screenshot_width=0
    )
    model = FakeModel(turns())
    assert run_agent(session, "goal", model).reason == "done"
    assert widths_sent(model, 2) == [full, full, full]


def test_a_step_width_not_below_the_full_width_changes_nothing(tmp_path):
    session = make_session(tmp_path, moves={"X": ("bag",)})
    full = session.source.latest().bgr.shape[1]
    session.config = dataclasses.replace(
        session.config, screenshot_width=full, step_screenshot_width=full
    )
    model = FakeModel(turns())
    assert run_agent(session, "goal", model).reason == "done"
    assert widths_sent(model, 2) == [full, full, full]

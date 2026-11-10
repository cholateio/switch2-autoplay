import itertools

from autoplay.core import clock


def test_now_is_non_decreasing():
    readings = [clock.now() for _ in range(10_000)]
    assert all(a <= b for a, b in itertools.pairwise(readings))
    assert readings[-1] > readings[0]

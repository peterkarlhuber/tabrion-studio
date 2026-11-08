from __future__ import annotations

import pytest

from tabrion_studio.idle import IdleMonitor


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_expires_after_the_timeout_and_touch_resets() -> None:
    clock = Clock()
    idle = IdleMonitor(60, clock=clock)
    clock.now += 59
    assert not idle.expired()
    clock.now += 1
    assert idle.expired()
    idle.touch()
    assert not idle.expired()
    assert idle.idle_for() == 0


def test_zero_disables() -> None:
    clock = Clock()
    idle = IdleMonitor(0, clock=clock)
    clock.now += 10**9
    assert not idle.enabled
    assert not idle.expired()


def test_negative_is_rejected() -> None:
    with pytest.raises(ValueError, match="negative"):
        IdleMonitor(-1)


@pytest.mark.parametrize(("timeout", "interval"), [(1, 0.2), (30, 3.0), (1800, 30.0)])
def test_check_interval(timeout: float, interval: float) -> None:
    assert IdleMonitor(timeout).check_interval() == interval

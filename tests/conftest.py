"""Fixtures shared by the suite: a fresh name per test, a stepped clock, an empty rig."""

from __future__ import annotations

import itertools
from collections.abc import Callable

import pytest
from flyball.rig import Rig
from flyball_sim import SteppedClock

_counter = itertools.count()


@pytest.fixture
def fresh() -> Callable[[str], str]:
    """A name no other test has used: ``fresh("hum")`` -> ``hum_17``."""
    return lambda stem: f"{stem}_{next(_counter)}"


@pytest.fixture
def clock() -> SteppedClock:
    return SteppedClock(0)


@pytest.fixture
def rig(clock: SteppedClock) -> Rig:
    rig = Rig()
    rig.clock = clock
    return rig

"""Fixtures shared by the suite: a fresh name per test, a stepped clock, an empty rig."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator

import pytest
from flyball_sim import SteppedClock

from flyball.model.catalog import Catalogs, set_catalog
from flyball.rig import Rig

_counter = itertools.count()


@pytest.fixture(scope="session", autouse=True)
def _catalog() -> Iterator[Catalogs]:
    catalog = Catalogs()
    catalog.discover()
    set_catalog(catalog)
    yield catalog
    set_catalog(None)


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

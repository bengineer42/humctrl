"""Units and quantities for the humidity rig.

`HUMIDITY` and `TEMPERATURE` are `flyball_linux`'s own -- `hum_sensors` is
built from its `sht4x_set`, so reusing its `Quantity` objects (not just
matching their symbols) is what makes a real reading and a sim one the same
quantity, `is`, not just `==`. `FLOW` and `EFFORT` are this rig's own: the
`Quantity`s the blender declares its
signals with -- name and unit, nothing else (range, precision and bands live
on the signal). The `Annotated` aliases below type plain pydantic fields
(a driver config's `max_flow`, a supply's `dry`/`wet`) where a bare float
would lose the unit in the schema -- a `Quantity` stands as its own
`Annotated` metadata, so the alias needs no separate `UnitRef`. Bounds are
the signal's `limits`, not the type's, so none are given here.
"""

from __future__ import annotations

from typing import Annotated

from flyball.core.quantity import Quantity
from flyball.core.typing import Normalised
from flyball.core.units.si import Litre, Minute, One
from flyball_linux.devices.chips.sht4x import HUMIDITY, TEMPERATURE, PercentRH

# --- units the SI does not name -------------------------------------------

LitrePerMinute = Litre / Minute

# --- quantities: a name and a unit, nothing else ---------------------------

FLOW = Quantity("flow", LitrePerMinute)
EFFORT = Quantity("effort", One)
"""A pump's drive, 0 to 1 of full."""
WET_FRACTION = Quantity("wet fraction", One)
"""The share of the blend drawn from the wet line, 0 to 1."""

# --- annotated floats, for pydantic config fields ---------------------------

Humidity = Annotated[float, HUMIDITY]
"""Relative humidity of a stream or the chamber."""

Temperature = Annotated[float, TEMPERATURE]
"""A sensor's temperature reading: absolute, on the Celsius scale."""

Flow = Annotated[float, FLOW]
"""Volumetric flow of a supply stream, as the pumps deliver it."""

WetFraction = Normalised
"""Share of the blend drawn from the wet stream. A ratio, not a quantity."""

__all__ = [
    "EFFORT",
    "FLOW",
    "HUMIDITY",
    "TEMPERATURE",
    "WET_FRACTION",
    "Flow",
    "Humidity",
    "LitrePerMinute",
    "PercentRH",
    "Temperature",
    "WetFraction",
]

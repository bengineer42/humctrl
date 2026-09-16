"""Units and quantities for the humidity rig.

`HUMIDITY`, `TEMPERATURE`, `FLOW` and `EFFORT` are the
[Quantity][flyball.core.quantity.Quantity]s the drivers declare their
signals with -- name and unit, nothing else (range, precision and bands live
on the signal). The `Annotated` aliases below type plain pydantic fields
(a driver config's `max_flow`, a supply's `dry`/`wet`) where a bare float
would lose the unit in the schema.
"""

from __future__ import annotations

from typing import Annotated

from flyball.core.quantity import Quantity
from flyball.core.typing import Normalised
from flyball.core.units import DIMENSIONLESS, UnitRef
from flyball.core.units.si import Celsius, Litre, Minute, One
from pydantic import Field

# --- units the SI does not name -------------------------------------------

PercentRH = DIMENSIONLESS.unit("percent relative humidity", "%RH", 0.01)
LitrePerMinute = Litre / Minute

# --- quantities: a name and a unit, nothing else ---------------------------

HUMIDITY = Quantity("humidity", PercentRH)
TEMPERATURE = Quantity("temperature", Celsius)
FLOW = Quantity("flow", LitrePerMinute)
EFFORT = Quantity("effort", One)
"""A pump's drive, 0 to 1 of full."""

# --- annotated floats, for pydantic config fields ---------------------------

Humidity = Annotated[float, UnitRef(PercentRH), Field(ge=0, le=100)]
"""Relative humidity of a stream or the chamber."""

Temperature = Annotated[float, UnitRef(Celsius)]
"""A sensor's temperature reading: absolute, on the Celsius scale."""

Flow = Annotated[float, UnitRef(LitrePerMinute), Field(ge=0)]
"""Volumetric flow of a supply stream, as the pumps deliver it."""

WetFraction = Normalised
"""Share of the blend drawn from the wet stream. A ratio, not a quantity."""

"""Units and annotated quantities for the humidity rig.

Every physical field is typed with a `Quantity` alias below, so the unit is
declared once and lands in the schema. Drivers report in exactly these units.
"""

from typing import Annotated

from flyball.core.typing import Normalised
from flyball.core.units import DIMENSIONLESS, UnitRef
from flyball.core.units.si import Celsius, Gram, Litre, Metre, Minute
from pydantic import Field

# --- units the SI does not name -------------------------------------------

PercentRH = DIMENSIONLESS.unit("percent relative humidity", "%RH", 0.01)
LitrePerMinute = Litre / Minute
GramPerCubicMetre = Gram / Metre**3

# --- annotated floats -------------------------------------------------------
# Written out as `Annotated` rather than through a helper: a type checker only
# accepts a type expression here, never the result of a call.

Humidity = Annotated[float, UnitRef(PercentRH), Field(ge=0, le=100)]
"""Relative humidity of a stream or the chamber."""

Temperature = Annotated[float, UnitRef(Celsius)]
"""A sensor's temperature reading: absolute, on the Celsius scale."""

AbsoluteHumidity = Annotated[float, UnitRef(GramPerCubicMetre), Field(ge=0)]
"""Water vapour mass per volume of air, for anything computed from RH and T."""

Flow = Annotated[float, UnitRef(LitrePerMinute), Field(ge=0)]
"""Volumetric flow of a supply stream, as the pumps deliver it."""

WetFraction = Normalised
"""Share of the blend drawn from the wet stream. A ratio, not a quantity."""

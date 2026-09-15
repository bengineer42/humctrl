"""Units and annotated quantities for the humidity rig.

Every state, config and command field with a physical meaning is typed with one
of the ``Quantity`` aliases below, so the unit is declared once, lands in the
schema, and is readable off the type in-process. Drivers report in exactly these
units; conversion from a device's native unit happens in the driver.
"""

from flyball.core.typing import Normalised
from flyball.units import DIMENSIONLESS, Quantity
from flyball.units.dimension import Milli
from flyball.units.si import Celsius, Gram, Litre, Metre, Minute

# --- units the SI does not name -------------------------------------------

PercentRH = DIMENSIONLESS.unit("percent relative humidity", "%RH", 0.01)
MilliLitrePerMinute = Litre.prefixed(Milli) / Minute
GramPerCubicMetre = Gram / Metre**3

# --- annotated floats -------------------------------------------------------

Humidity = Quantity(PercentRH, ge=0, le=100)
"""Relative humidity of a stream or the chamber."""

Temperature = Quantity(Celsius)
"""A sensor's temperature reading: absolute, on the Celsius scale."""

AbsoluteHumidity = Quantity(GramPerCubicMetre, ge=0)
"""Water vapour mass per volume of air, for anything computed from RH and T."""

Flow = Quantity(MilliLitrePerMinute, ge=0)
"""Volumetric flow of a supply stream, as the pumps deliver it."""

WetFraction = Normalised
"""Share of the blend drawn from the wet stream. A ratio, not a quantity."""

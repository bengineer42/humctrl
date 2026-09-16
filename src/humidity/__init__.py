"""Registers this package's tagged configs (links and device drivers).

Importing `humidity` -- directly, or through the `flyball.configs` entry
point `discover()` reads -- makes `i2c`, `linux_pwm`, `sht4x`, `sht4x_set`,
`dual_pump_blender` and `sim_humidity_chamber` valid in a rig file.
"""

from __future__ import annotations

import humidity.blender as blender
import humidity.links as links
import humidity.sht4x as sht4x
import humidity.sim as sim

__all__ = ["blender", "links", "sht4x", "sim"]

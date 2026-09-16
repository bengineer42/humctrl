"""Registers this package's tagged configs (the blender driver and the simulated plant).

Importing `humidity` -- directly, or through the `flyball.configs` entry
point `discover()` reads -- makes `dual_pump_blender` and
`sim_humidity_chamber` valid in a rig file. `i2c`, `sht4x`, `sht4x_set` and
`pwm` come from `flyball-linux`, a dependency, so importing it too is
enough to register those (rig.yaml/sim.yaml don't need this package to do
it explicitly).
"""

from __future__ import annotations

import humidity.blender as blender
import humidity.sim as sim

__all__ = ["blender", "sim"]

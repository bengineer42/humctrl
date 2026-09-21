"""The blender driver and the simulated plant.

`humidity.configs.register` is the `flyball.configs` entry point that makes
`dual_pump_blender` and `sim_humidity_chamber` valid tags in a rig file --
explicit, not a side effect of importing this package. `i2c`, `sht4x`,
`sht4x_set` and `pwm` come from `flyball-linux`, a dependency, whose own
entry point registers those.
"""

from __future__ import annotations

import humidity.blender as blender
import humidity.sim as sim

__all__ = ["blender", "sim"]

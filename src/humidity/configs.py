"""The entry-point target: explicitly registers this package's tagged configs.

`i2c`/`sht4x`/`sht4x_set`/`pwm` come from `flyball-linux`, a dependency, and
that package's own `flyball.configs` entry point registers those -- nothing
here needs to re-register them, `discover()` walks every installed entry
point, this package's own included.
"""

from flyball.model.catalog import Catalogs

from humidity.blender import DualPumpBlenderConfig
from humidity.sim import HumidityChamberConfig


def register(catalog: Catalogs) -> None:
    catalog.register_device(DualPumpBlenderConfig)
    catalog.register_link(HumidityChamberConfig)

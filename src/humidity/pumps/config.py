from flyball.core.config import Config, ConfigOr, resolve
from pydantic import ConfigDict

from humidity.pumps.types import MaxFlowsDefault, MaxFlowsLike

from .drivers import DualPumpDriver
from .dual import DualPumps


class DualPumpsConfig(Config[DualPumps]):
    # The driver is a protocol and the flow types are plain classes: neither
    # has a pydantic schema, and neither needs one to be built from code.
    model_config = ConfigDict(arbitrary_types_allowed=True)

    units: str | None = None
    max_flows: MaxFlowsLike = MaxFlowsDefault
    driver: ConfigOr[DualPumpDriver]

    def build(self) -> DualPumps:
        return DualPumps(
            pumps=resolve(self.driver),
            units=self.units,
            max_flows=self.max_flows,
        )

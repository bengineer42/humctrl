from flyball.core.config import Config, ConfigOr, resolve
from pydantic import ConfigDict, field_serializer

from humidity.pumps.types import MaxFlows, MaxFlowsDefault

from .drivers import DualPumpDriver
from .dual import DualPumps


class DualPumpsConfig(Config[DualPumps]):
    # The driver is a protocol and the flow types are plain classes: neither
    # has a pydantic schema, and neither needs one to be built from code.
    model_config = ConfigDict(arbitrary_types_allowed=True)

    units: str | None = None
    max_flows: MaxFlows = MaxFlowsDefault
    driver: ConfigOr[DualPumpDriver]

    @field_serializer("driver")
    def _serialize_driver(self, driver: ConfigOr[DualPumpDriver]) -> object:
        """A built driver has no wire form; report what it is rather than fail the view."""
        return driver if isinstance(driver, Config) else {"type": type(driver).__name__}

    def build(self) -> DualPumps:
        return DualPumps(
            pumps=resolve(self.driver),
            units=self.units,
            max_flows=self.max_flows,
        )

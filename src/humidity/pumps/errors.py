from __future__ import annotations

from functools import cached_property

from flyball.foundation.errors import FlyballError, HardwareError, UnachievableError
from flyball.foundation.quantities import format_quantity
from flyball.foundation.typing import Normalised, Positive

from .types import MaxFlows, SupplyFlows


class PumpError(FlyballError):
    """Base for everything humidity.pumps raises."""


class PumpErrorGroup(ExceptionGroup, PumpError): ...


class FlowError(PumpError, UnachievableError):
    """A requested flow or fraction cannot be applied."""


class FlowsOverdrivenError(FlowError):
    flows: SupplyFlows
    max_flow: Positive
    max_flows: MaxFlows
    wet_fraction: Normalised | None
    units: str | None

    def flow_exceeds_max(self, name: str, flow: float, max_flow: float) -> str:
        flow_str = format_quantity(flow, units=self.units)
        max_flow_str = format_quantity(max_flow, units=self.units)
        return f"\n\r{name} flow {flow_str} exceeds {max_flow_str}"

    def __init__(
        self,
        flows: SupplyFlows,
        max_flows: MaxFlows,
        max_flow: Positive | None = None,
        wet_fraction: Normalised | None = None,
        units: str | None = None,
    ) -> None:
        self.flows = flows
        self.max_flow = max_flow or (max_flows.dry + max_flows.wet)
        self.max_flows = max_flows
        self.wet_fraction = wet_fraction
        self.units = units
        string = "Flow unreachable."
        if max_flow is not None and self.total_overdriven:
            string += self.flow_exceeds_max("Total", self.flows.total, max_flow)
            if wet_fraction is not None:
                string += f" at wet fraction {wet_fraction:.3f}"
            string += "."
        if self.dry_overdriven:
            string += self.flow_exceeds_max("Dry", self.flows.dry, max_flows.dry) + "."
        if self.wet_overdriven:
            string += self.flow_exceeds_max("Wet", self.flows.wet, max_flows.wet) + "."

        super().__init__(string)

    @cached_property
    def dry_overdriven(self) -> bool:
        return self.flows.dry > self.max_flows.dry

    @cached_property
    def wet_overdriven(self) -> bool:
        return self.flows.wet > self.max_flows.wet

    @cached_property
    def total_overdriven(self) -> bool:
        return self.flows.total > self.max_flow


class PumpHardwareError(PumpError, HardwareError):
    """The underlying device failed."""

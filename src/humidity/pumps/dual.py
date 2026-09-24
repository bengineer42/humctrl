from typing import NoReturn

from flyball.foundation.quantities import format_quantity
from flyball.foundation.typing import NonNegative, Normalised, Positive

from .drivers import DualPumpDriver
from .errors import FlowsOverdrivenError
from .types import (
    Absolute,
    CurrentBlend,
    FixedBlendFlow,
    MaxFlows,
    OfBlendMax,
    OfGuaranteedMax,
    OnOverdrive,
    PumpsLimits,
    PumpsState,
    PumpState,
    PumpsView,
    SupplyEfforts,
    SupplyFlows,
)


class DualPumps:
    pumps: DualPumpDriver
    units: str | None = None
    max_flows: MaxFlows

    def __init__(
        self,
        pumps: DualPumpDriver,
        max_flows: MaxFlows,
        units: str | None = None,
    ) -> None:
        self.pumps = pumps
        self.max_flows = max_flows
        self.units = units

    @property
    def dry_max_flow(self) -> Positive:
        return self.max_flows.dry

    @property
    def wet_max_flow(self) -> Positive:
        return self.max_flows.wet

    @property
    def guaranteed_max_flow(self) -> Positive:
        return self.max_flows.guaranteed

    @property
    def limits(self) -> PumpsLimits:
        return PumpsLimits(
            max_flows=self.max_flows, guaranteed_max_flow=self.guaranteed_max_flow, units=self.units
        )

    @property
    def dry_effort(self) -> Normalised:
        return self.pumps.dry_effort

    @property
    def wet_effort(self) -> Normalised:
        return self.pumps.wet_effort

    @property
    def efforts(self) -> SupplyEfforts:
        return self.pumps.efforts

    @property
    def dry_flow(self) -> NonNegative:
        return self.dry_max_flow * self.dry_effort

    @property
    def wet_flow(self) -> NonNegative:
        return self.wet_max_flow * self.wet_effort

    @property
    def flows(self) -> SupplyFlows:
        return self.efforts.to_flows(self.max_flows)

    @property
    def dry_output(self) -> PumpState:
        return PumpState(effort=self.dry_effort, flow=self.dry_flow)

    @property
    def wet_output(self) -> PumpState:
        return PumpState(effort=self.wet_effort, flow=self.wet_flow)

    @property
    def output(self) -> PumpsState:
        return self.efforts_to_outputs(self.efforts)

    @property
    def blend(self) -> CurrentBlend:
        return self.flows.blend

    @property
    def total_flow(self) -> NonNegative:
        return self.flows.total

    @property
    def dry_fraction(self) -> Normalised:
        return self.flows.dry_fraction

    @property
    def wet_fraction(self) -> Normalised:
        return self.flows.wet_fraction

    @property
    def blend_effort(self) -> Normalised:
        return self.efforts.max

    @property
    def view(self) -> PumpsView:
        return PumpsView(self.limits, self.output)

    def flow_str(self, flow: NonNegative) -> str:
        return format_quantity(flow, units=self.units)

    def flows_to_efforts(self, flows: SupplyFlows) -> SupplyEfforts:
        return self.max_flows.to_efforts(flows)

    def validate_flows(
        self,
        flows: SupplyFlows,
        wet_fraction: Normalised | None = None,
    ) -> SupplyEfforts:
        efforts = self.flows_to_efforts(flows)
        if efforts.overdriven:
            self.raise_flow_overdriven(flows, wet_fraction)
        return efforts

    def raise_flow_overdriven(
        self, flows: SupplyFlows, wet_fraction: Normalised | None = None
    ) -> NoReturn:
        raise FlowsOverdrivenError(
            flows=flows,
            max_flows=self.max_flows,
            units=self.units,
            wet_fraction=wet_fraction,
        )

    def set_blend(
        self,
        flow: FixedBlendFlow,
        wet_fraction: Normalised,
        caps: MaxFlows | None = None,
    ) -> PumpsState:
        """Blend `wet_fraction` at `flow`, allocated inside `caps` (default: the pumps' maxima).

        `caps` are the flows each line may be asked for now -- the blender's
        effective `flows.*` limits, which a rig file may narrow below the
        pumps' `max_flow`. The efforts are still worked out against the
        pumps' own maxima.
        """
        caps = self.max_flows if caps is None else caps
        if isinstance(flow, Absolute):
            flows = SupplyFlows.from_blend(flow.flow, wet_fraction)
            if flow.on_overdrive == OnOverdrive.RAISE and not flows.is_valid(caps):
                raise FlowsOverdrivenError(
                    flows=flows, max_flows=caps, units=self.units, wet_fraction=wet_fraction
                )
            flows = flows.derated(caps)
        elif isinstance(flow, OfBlendMax):
            flows = caps.flows_at_blend(wet_fraction) * flow.blend_fraction
        elif isinstance(flow, OfGuaranteedMax):
            flows = SupplyFlows.from_blend(
                flow.guaranteed_max_fraction * caps.guaranteed, wet_fraction
            )
        else:
            raise TypeError(f"{flow!r} is resolved by the blender before it reaches the pumps")
        efforts = flows.to_efforts(self.max_flows)

        return self.set_efforts(efforts)

    def efforts_to_outputs(self, efforts: SupplyEfforts) -> PumpsState:
        return PumpsState(efforts=efforts, flows=self.max_flows.to_flows(efforts))

    def set_flows(self, flows: SupplyFlows) -> PumpsState:
        efforts = self.validate_flows(flows)
        return self.set_efforts(efforts)

    def set_efforts(self, efforts: SupplyEfforts) -> PumpsState:
        return self.efforts_to_outputs(self.pumps.set_efforts(efforts))

    def stop(self) -> None:
        self.pumps.stop()

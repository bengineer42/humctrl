from dataclasses import dataclass
from enum import Enum
from threading import RLock

from flyball.control import Actuator
from flyball.core import Normalised, Observer, Percent, Positive, Reading, require
from flyball.core.errors import NotReadyError, UnachievableError

from humidity.pumps import (
    BlendFlow,
    DefaultBlendFlow,
    DualPumps,
    PumpsState,
    SupplyFlows,
)
from humidity.pumps.types import (
    MaxFlows,
    MutSupplyHumidities,
    PumpsMode,
    SupplyEffortsLike,
    SupplyFlowsLike,
    SupplyHumidities,
    SupplyHumiditiesLike,
)
from humidity.readers import HTSource


class BlenderError(Exception): ...


class HumidityRailError(BlenderError, UnachievableError):
    """The target humidity is outside the range the two lines can mix to.

    Reported on :class:`StreamState` rather than raised: the blend rails to the
    nearest achievable end and the run continues. No amount of pump capacity
    fixes it, so the remedy is a wetter or drier supply, not more flow.
    """

    def __init__(
        self, dry_humidity: Percent, wet_humidity: Percent, target_humidity: Percent
    ) -> None:
        super().__init__(
            f"Target humidity ({target_humidity}%) is outside the achievable range "
            f"{dry_humidity}% (dry) to {wet_humidity}% (wet)."
        )


class SupplyHumiditiesError(BlenderError, UnachievableError):
    """The wet and dry line humidities are not in the expected order.

    Raised rather than reported: with no span between the lines there is no
    blend to compute, so the mixing model cannot produce an answer at all.
    """

    def __init__(self, humidities: SupplyHumidities | MutSupplyHumidities) -> None:
        super().__init__(
            f"Wet ({humidities.wet}%) and dry ({humidities.dry}%) humidities are not in the "
            "expected order. Wet humidity must be greater than dry humidity."
        )


class DemandNotSetError(NotReadyError):
    def __init__(self) -> None:
        super().__init__("Demand not set. Use set_demand() to set the demand before using it.")


class Rail(Enum):
    WET = "wet"
    DRY = "dry"

    def __float__(self) -> float:
        match self:
            case Rail.WET:
                return 1.0
            case Rail.DRY:
                return 0.0


def expected_humidity_from_fraction(
    humidities: SupplyHumidities, wet_fraction: Normalised
) -> Percent:
    return humidities.dry + wet_fraction * humidities.difference


def expected_humidity_from_flows(
    flows: SupplyFlows, humidities: SupplyHumidities
) -> Percent | None:
    total = flows.total
    return None if total else (flows * humidities).total / total


def calculate_wet_fraction(
    humidities: SupplyHumidities | MutSupplyHumidities, target: Percent
) -> Normalised | Rail:

    if humidities.wet <= humidities.dry:
        raise SupplyHumiditiesError(humidities)
    if target < humidities.dry:
        return Rail.DRY
    if target > humidities.wet:
        return Rail.WET
    return (target - humidities.dry) / humidities.difference


@dataclass(frozen=True, slots=True)
class BlenderState(PumpsState):
    humidities: SupplyHumidities
    demand: Percent | None
    expected_humidity: Percent | None


@dataclass(frozen=True, slots=True)
class BlenderConfig:
    max_flows: MaxFlows
    full_range_max_flow: Positive
    flow_units: str | None
    blend_flow: BlendFlow


@dataclass(frozen=True, slots=True)
class BlenderView(BlenderState):
    max_flows: MaxFlows
    full_range_max_flow: Positive
    flow_units: str | None
    blend_flow: BlendFlow

    @classmethod
    def of(cls, spec: BlenderConfig, state: BlenderState) -> "BlenderView":
        return cls(
            humidities=state.humidities,
            demand=state.demand,
            flows=state.flows,
            efforts=state.efforts,
            expected_humidity=state.expected_humidity,
            blend_flow=spec.blend_flow,
            full_range_max_flow=spec.full_range_max_flow,
            flow_units=spec.flow_units,
            max_flows=spec.max_flows,
        )


class DualPumpsBlender(Actuator, Observer):
    pumps: DualPumps
    _humidities: MutSupplyHumidities
    lock: RLock
    _demand: Percent | None
    output: PumpsState
    blend_flow: BlendFlow
    expected_humidity: Percent | None
    _updated: bool = False
    dry: HTSource | None = None
    wet: HTSource | None = None

    def __init__(
        self,
        pumps: DualPumps,
        humidities: SupplyHumiditiesLike,
        demand: Percent | None = None,
        flow: BlendFlow = DefaultBlendFlow,
        name: str = "pumps",
    ) -> None:
        super().__init__(name)
        self.pumps = pumps
        self.blend_flow = flow
        self._demand = demand
        self._humidities = MutSupplyHumidities.of(humidities)
        self.expected_humidity = None
        self.lock = RLock()
        self.touches = frozenset((self,))

    @property
    def supply_humidities(self) -> SupplyHumidities:
        return SupplyHumidities.of_dry_wet(self._humidities)

    @property
    def demand(self) -> Percent | None:
        return self._demand

    @property
    def required_demand(self) -> Percent:
        return require(self._demand, DemandNotSetError)

    @property
    def spec(self) -> BlenderConfig:
        pumps = self.pumps.spec
        return BlenderConfig(
            max_flows=pumps.max_flows,
            full_range_max_flow=pumps.guaranteed_max_flow,
            flow_units=pumps.units,
            blend_flow=self.blend_flow,
        )

    @property
    def state(self) -> BlenderState:
        return BlenderState(
            humidities=self.supply_humidities,
            demand=self._demand,
            flows=self.output.flows,
            efforts=self.output.efforts,
            expected_humidity=self.expected_humidity,
        )

    @property
    def view(self) -> BlenderView:
        return BlenderView.of(self.spec, self.state)

    def set_channels(self, dry: HTSource | None, wet: HTSource | None) -> None:
        """Which supply sensors to follow. Call before attaching to the rig."""
        self.dry = dry
        self.wet = wet
        self.observes = frozenset(source for source in (dry, wet) if source is not None)

    def _update_demand(self, demand: Percent) -> None:
        if self._demand != demand:
            self._updated = True
        self._demand = demand

    def _update_flow(self, flow: BlendFlow) -> None:
        if self.blend_flow != flow:
            self._updated = True
        self.blend_flow = flow

    def _update_readings(self, dry: Percent | None = None, wet: Percent | None = None) -> None:
        if dry is not None and self._humidities.dry != dry:
            self._updated = True
            self._humidities.dry = dry
        if wet is not None and wet != self._humidities.wet:
            self._updated = True
            self._humidities.wet = wet

    def set_supply_flows(self, flows: SupplyFlowsLike) -> PumpsState:
        with self.lock:
            return self._update_outputs(self.pumps.set_flows(flows))

    def set_blend(self, flow: BlendFlow, wet_fraction: Normalised) -> PumpsState:
        with self.lock:
            return self._update_outputs(self.pumps.set_blend(flow, wet_fraction))

    def set_supply_efforts(self, efforts: SupplyEffortsLike) -> PumpsState:
        with self.lock:
            return self._update_outputs(self.pumps.set_efforts(efforts))

    def set_pumps(self, pump_mode: PumpsMode) -> PumpsState:
        with self.lock:
            return self._update_outputs(self.pumps.set_mode(pump_mode))

    def stop_pumps(self) -> None:
        with self.lock:
            self.pumps.stop()
            self._update_outputs(self.pumps.output)

    def _update(
        self,
        demand: Percent | None,
        dry: Percent | None = None,
        wet: Percent | None = None,
        flow: BlendFlow | None = None,
    ) -> bool:
        self._update_readings(dry, wet)
        if demand is not None:
            self._update_demand(demand)
        if flow is not None:
            self._update_flow(flow)
        return self._updated

    def update_flow(self, flow: BlendFlow) -> None:
        with self.lock:
            self._update_flow(flow)

    def update_demand(self, demand: Percent) -> None:
        with self.lock:
            self._update_demand(demand)

    def update_readings(self, dry: Percent | None = None, wet: Percent | None = None) -> None:
        with self.lock:
            self._update_readings(dry, wet)

    def update(
        self,
        demand: Percent | None,
        dry: Percent | None = None,
        wet: Percent | None = None,
        flow: BlendFlow | None = None,
    ) -> bool:
        with self.lock:
            return self._update(demand, dry, wet, flow)

    def _update_outputs(self, output: PumpsState) -> PumpsState:
        self.pump_error = None
        self.output = output
        self.expected_humidity = expected_humidity_from_flows(
            self.output.flows, self.supply_humidities
        )
        return output

    def _apply(self) -> None:
        if self._updated and self.demand is not None:
            fraction = calculate_wet_fraction(self._humidities, self.demand)
            self._update_outputs(self.pumps.set_blend(self.blend_flow, float(fraction)))
            self._updated = False

    def update_blend(
        self,
        demand: Percent | None = None,
        dry: Percent | None = None,
        wet: Percent | None = None,
        flow: BlendFlow | None = None,
    ) -> None:
        with self.lock:
            self._update(demand, dry, wet, flow)
            self._apply()

    def set_demand(self, demand: float) -> None:
        with self.lock:
            self._update_demand(demand)

    def observe(self, reading: Reading) -> None:
        match reading.source:
            case self.wet:
                self.update_readings(wet=reading.value)
            case self.dry:
                self.update_readings(dry=reading.value)

    def apply(self) -> None:
        with self.lock:
            self._apply()

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import RLock

from flyball.core import Normalised, Observer, Percent, Reading, require
from flyball.core.errors import NotReadyError, UnachievableError
from flyball.core.sink import Actuator, ActuatorConfig, ActuatorState, command

from humidity.pumps import (
    BlendFlow,
    DefaultBlendFlow,
    DualPumps,
    MutSupplyHumidities,
    PumpsState,
    SupplyEfforts,
    SupplyFlows,
    SupplyHumidities,
    SupplyHumiditiesLike,
)
from humidity.pumps.config import DualPumpsConfig
from humidity.readers import HTSource
from humidity.units import Flow, Humidity, PercentRH


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
    return (flows * humidities).total / total if total else None


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


@dataclass(frozen=True, slots=True, kw_only=True)
class BlenderState(ActuatorState):
    demand: Humidity | None = None  # narrows the base field; kw_only makes the order legal
    flows: SupplyFlows
    efforts: SupplyEfforts
    humidities: SupplyHumidities
    expected_humidity: Humidity | None


class BlenderConfig(ActuatorConfig["DualPumpsBlender"]):
    pumps: DualPumpsConfig
    humidities: SupplyHumidities
    blend_flow: BlendFlow = DefaultBlendFlow
    default_demand: Humidity | None = None
    name: str = "pumps"

    def build(self) -> DualPumpsBlender:
        return DualPumpsBlender(
            self.pumps.build(),
            self.humidities,
            flow=self.blend_flow,
            name=self.name,
            demand=self.default_demand,
            config=self,
        )


# What a command accepts over the wire: the ``*Like`` aliases also admit the
# in-process ``DryWetOps`` classes, which have no schema.
type FlowsIn = SupplyFlows | tuple[Flow, Flow] | Flow
type EffortsIn = SupplyEfforts | tuple[Normalised, Normalised] | Normalised


class DualPumpsBlender(Actuator[BlenderConfig, BlenderState], Observer):
    demand_unit = PercentRH

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
        config: BlenderConfig | None = None,
    ) -> None:
        super().__init__(name)
        self.pumps = pumps
        self.blend_flow = flow
        self._demand = demand
        self._humidities = MutSupplyHumidities.of(humidities)
        self.output = pumps.output
        self.expected_humidity = None
        self.lock = RLock()
        self.touches = frozenset((self,))
        self._config = config

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
    def config(self) -> BlenderConfig:
        """The config this was built from, or one describing it around the live driver."""
        if self._config is not None:
            return self._config
        return BlenderConfig(
            pumps=DualPumpsConfig(
                units=self.pumps.units, max_flows=self.pumps.max_flows, driver=self.pumps.pumps
            ),
            humidities=self.supply_humidities,
            blend_flow=self.blend_flow,
            default_demand=self._demand,
            name=self.name,
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

    @command(tag="set_flows")
    def set_supply_flows(self, flows: FlowsIn) -> PumpsState:
        """Drive each pump at a flow; one number sets both."""
        with self.lock:
            return self._update_outputs(self.pumps.set_flows(flows))

    @command
    def set_blend(self, flow: BlendFlow, wet_fraction: Normalised) -> PumpsState:
        """Split a total flow between the lines by wet fraction."""
        with self.lock:
            return self._update_outputs(self.pumps.set_blend(flow, wet_fraction))

    @command(tag="set_efforts")
    def set_supply_efforts(self, efforts: EffortsIn) -> PumpsState:
        """Drive each pump at a fraction of full effort; one number sets both."""
        with self.lock:
            return self._update_outputs(self.pumps.set_efforts(efforts))

    @command(tag="stop")
    def stop_pumps(self) -> None:
        """Stop both pumps."""
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

    @command(tag="blend")
    def update_blend(
        self,
        demand: Humidity | None = None,
        dry: Humidity | None = None,
        wet: Humidity | None = None,
        flow: BlendFlow | None = None,
    ) -> None:
        """Re-blend for a new demand, supply humidities or total flow; omitted ones stand."""
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

"""The dual-pump blender: a composite actuator that mixes a dry and a wet line to a target %RH.

A controller drives `humidity`; people run the commands. `set_flows`,
`set_efforts` and `stop` drive the lines directly and put the blender in
that mode; a humidity demand puts it back in BLEND, where `commit` does the
split-range arithmetic once per delivery. `mode` says which is in force.
"""

from __future__ import annotations

from typing import Annotated

from flyball.foundation.device import (
    Access,
    Committable,
    Demand,
    DriverConfig,
    Limit,
    Namespace,
    Output,
    Section,
    command,
)
from flyball.foundation.errors import UnachievableError
from flyball.foundation.primitives import Labelled
from flyball.foundation.typing import Normalised, Positive
from flyball_linux.links.pwm import PwmLinkConfig
from pydantic import BaseModel, ConfigDict

from humidity.pumps import (
    Absolute,
    BlendFlow,
    DefaultBlendFlow,
    DefaultHumidities,
    DualPumps,
    MaxFlows,
    OnOverdrive,
    PumpPair,
    PwmPump,
    SupplyEfforts,
    SupplyFlows,
    SupplyHumidities,
)
from humidity.units import EFFORT, FLOW, HUMIDITY, WET_FRACTION, Flow, Humidity


class BlenderError(Exception): ...


class HumidityRailError(BlenderError, UnachievableError):
    """The target humidity is outside the range the two lines can mix to.

    Not raised: the blend rails to the nearer end and `humidity`'s
    `WriteState.at_limit` says so; the remedy is a wetter or drier supply.
    """


class SupplyHumiditiesError(BlenderError, UnachievableError):
    """The wet and dry line humidities are not in order. Raised: with no span there is no blend."""

    def __init__(self, humidities: SupplyHumidities) -> None:
        super().__init__(
            f"Wet ({humidities.wet}%) and dry ({humidities.dry}%) humidities are not in the "
            "expected order. Wet humidity must be greater than dry humidity."
        )


def expected_humidity_from_flows(flows: SupplyFlows, humidities: SupplyHumidities) -> float | None:
    total = flows.total
    return (flows * humidities).total / total if total else None


def expected_humidity_from_wet_fraction(
    wet_fraction: Normalised | Limit, humidities: SupplyHumidities
) -> float:
    if wet_fraction is Limit.LOW:
        return humidities.dry
    if wet_fraction is Limit.HIGH:
        return humidities.wet
    return humidities.dry + wet_fraction * humidities.difference


def calculate_wet_fraction(humidities: SupplyHumidities, target: float) -> Normalised | Limit:
    """The wet fraction for `target`, or the end it rails to: LOW is all dry, HIGH all wet."""
    if humidities.wet <= humidities.dry:
        raise SupplyHumiditiesError(humidities)
    if target < humidities.dry:
        return Limit.LOW
    if target > humidities.wet:
        return Limit.HIGH
    return (target - humidities.dry) / humidities.difference


class Mode(Labelled):
    """What is driving the pumps: a humidity demand, or the last command by hand. Starts manual."""

    BLEND = "blend", "Blending to a target humidity"
    MANUAL = "manual", "Set by hand"


DRY = Section("dry", "Dry line")
WET = Section("wet", "Wet line")


class DualPumpBlender(Committable):
    """Two pumps, blended to a target %RH; also settable directly by flow or by effort.

    A demand on `humidity` puts the blender in BLEND: `commit` does the
    split-range arithmetic once per delivery (`calculate_wet_fraction`),
    however many of a new target, a changed supply reading and a new blend
    flow arrived together -- one pump write. `set_flows`, `set_efforts` and
    `stop` drive the lines at once and change the mode, so a supply reading
    re-blends only while blending. The flows and efforts are demands whose
    readbacks follow whatever is driving the pumps.
    """

    flows = Namespace("flows", "Flows")
    efforts = Namespace("efforts", "Efforts")
    max_flows = Namespace("max_flows", "Max flows")
    humidities = Namespace("humidities", "Flow humidities")
    supply_defaults = Namespace("supply_defaults", "Supply humidities when unbound")

    dry_max_flow = max_flows.config(DRY, "Dry max flow", FLOW)
    wet_max_flow = max_flows.config(WET, "Wet max flow", FLOW)
    dry_supply_default = supply_defaults.config(DRY, "Dry line humidity", HUMIDITY)
    wet_supply_default = supply_defaults.config(WET, "Wet line humidity", HUMIDITY)

    dry_supply = humidities.input(DRY, "Dry line humidity", HUMIDITY, default=dry_supply_default)
    wet_supply = humidities.input(WET, "Wet line humidity", HUMIDITY, default=wet_supply_default)

    humidity = Demand("humidity", "Target humidity", HUMIDITY, limits=(dry_supply, wet_supply))
    """Clamped to what the lines can mix: the supply humidities, as they read now."""
    # Readbacks only: `set_flows`/`set_efforts` are the only way to move these -- see
    # `commit`, which never looks at their `.pending`. Not `access=Access.RPW`'s default for
    # a Demand, so the generic signal editor does not offer a direct write that would be
    # silently accepted and never reach the pumps.
    dry_flow = flows.demand(
        DRY, "Dry pump flow", FLOW, limits=(0.0, dry_max_flow), access=Access.RP
    )
    wet_flow = flows.demand(
        WET, "Wet pump flow", FLOW, limits=(0.0, wet_max_flow), access=Access.RP
    )
    dry_effort = efforts.demand(DRY, "Dry pump effort", EFFORT, limits=(0.0, 1.0), access=Access.RP)
    wet_effort = efforts.demand(WET, "Wet pump effort", EFFORT, limits=(0.0, 1.0), access=Access.RP)

    expected_humidity = Output(
        "expected_humidity", "Expected humidity", HUMIDITY, range=(0.0, 100.0), precision=1
    )
    mode = Output("mode", "Mode", vtype=Mode, initial=Mode.MANUAL)
    blend = Namespace("blend", "Blend")
    blend_flow = blend.setting("flow", "Blend flow", vtype=BlendFlow, initial=DefaultBlendFlow)
    wet_fraction = blend.demand("wet_fraction", "Wet fraction", WET_FRACTION, limits=(0.0, 1.0))

    def __init__(
        self,
        name: str,
        pumps: DualPumps,
        *,
        supply: SupplyHumidities = DefaultHumidities,
        blend_flow: Positive = 1.0,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self._pumps = pumps
        self._target = (supply.dry + supply.wet) / 2.0
        self.dry_max_flow.push(pumps.dry_max_flow)
        self.wet_max_flow.push(pumps.wet_max_flow)
        self.dry_supply_default.push(supply.dry)
        self.wet_supply_default.push(supply.wet)
        self.blend_flow.push(Absolute(blend_flow, OnOverdrive.CLAMP))
        self._push_readbacks()  # the pumps as found: every demand has a value from the start

    @property
    def _supply(self) -> SupplyHumidities:
        """The supply lines' humidity now: the bound sensors', or the config's."""
        return SupplyHumidities(dry=self.dry_supply.value, wet=self.wet_supply.value)

    def commit(self, time_ns: int) -> None:
        """A humidity demand starts blending; while blending, a moved supply re-blends."""
        if (target := self.humidity.pending) is not None:
            self._target = target
            if self.mode.value is not Mode.BLEND:
                self.mode.push(Mode.BLEND, time_ns)
        if self.mode.value is Mode.BLEND:
            self._blend_pumps(time_ns)

    def _blend_pumps(self, time_ns: int | None = None, blend: BlendFlow | None = None) -> None:
        """Put the blend on the pumps: the wet fraction for the target, the flow `blend` says."""
        fraction = calculate_wet_fraction(self._supply, self._target)
        railed = isinstance(fraction, Limit)
        self.humidity.at_limit = fraction if railed else None
        wet = fraction.fraction if isinstance(fraction, Limit) else fraction
        self._set_blend(time_ns, self.blend_flow.value if blend is None else blend, wet)

    def _set_blend(self, time_ns: int | None, blend: BlendFlow, wet: float) -> None:
        """One pump write for a blend, then every readback and the setting at one instant."""
        self._pumps.set_blend(blend, wet)
        self._push_readbacks(time_ns, blend=blend)

    def _push_readbacks(
        self,
        time_ns: int | None = None,
        blend: BlendFlow | None = None,
        wet_fraction: float | None = None,
    ) -> None:
        """What the pumps are now doing, on the flow and effort demands, and what it delivers.

        `blend` only when given, `wet_fraction` the pumps' own unless given;
        `expected_humidity` not at all with no flow: a None is not pushed.
        """
        output = self._pumps.output
        self.push(
            time_ns,
            wet_fraction=output.flows.wet_fraction if wet_fraction is None else wet_fraction,
            dry_flow=output.flows.dry,
            wet_flow=output.flows.wet,
            dry_effort=output.efforts.dry,
            wet_effort=output.efforts.wet,
            expected_humidity=expected_humidity_from_flows(output.flows, self._supply),
            blend_flow=blend,
        )

    @command
    def set_blend(self, blend_flow: BlendFlow) -> None:
        """Choose how much air the blend moves.

        An absolute flow (and what to do if the lines cannot give it), a
        fraction of the most the blend can move at this mix, or a fraction of
        the flow guaranteed at every mix. Takes effect at once when blending,
        else at the next blend.
        """
        if self.mode.value is Mode.BLEND:
            self._blend_pumps(blend=blend_flow)  # may refuse (overdrive): then the setting stands
        else:
            self.blend_flow.push(blend_flow)

    @command(mode=Mode.BLEND, interrupts=True)
    def set_humidity(self, humidity: Humidity, blend_flow: BlendFlow | None = None) -> None:
        """Blend to a humidity at a blend flow, by hand: the controller, if any, goes to manual.

        Either left out keeps its current value. What a controller does
        through the `humidity` demand, done in one go from a program or a form.
        """
        self._target = humidity
        self._blend_pumps(blend=blend_flow)

    @command(mode=Mode.MANUAL, interrupts=True)
    def set_fraction(self, blend_flow: BlendFlow, wet_fraction: float) -> None:
        """Blend at a wet fraction by hand, at a blend flow; either left out keeps its value."""
        self._set_blend(None, blend_flow, wet=wet_fraction)

    @command(mode=Mode.MANUAL, interrupts=True)
    def set_flows(self, dry: Annotated[Flow, dry_flow], wet: Annotated[Flow, wet_flow]) -> None:
        """Drive each line at a flow. A line left out keeps its current flow."""
        self._pumps.set_flows(SupplyFlows(dry, wet))
        self._push_readbacks()

    @command(mode=Mode.MANUAL, interrupts=True)
    def set_efforts(
        self, dry: Annotated[Normalised, dry_effort], wet: Annotated[Normalised, wet_effort]
    ) -> None:
        """Drive each line at an effort, 0-1 of full. A line left out keeps its current effort."""
        self._pumps.set_efforts(SupplyEfforts(dry, wet))
        self._push_readbacks()

    @command(mode=Mode.MANUAL, interrupts=True)
    def stop(self) -> None:
        """Stop both pumps at once; a controller driving the target goes to manual."""
        self._pumps.stop()
        self._push_readbacks()


class PumpLineConfig(BaseModel):
    """One line's PWM channel and its limits."""

    model_config = ConfigDict(extra="forbid")

    channel: int
    deadband: Normalised = 0.0
    max_flow: Positive


class SupplyConfig(BaseModel):
    """The supply lines' humidity, when not followed from a sensor (`bound`)."""

    model_config = ConfigDict(extra="forbid")

    dry: Humidity
    wet: Humidity


class DualPumpBlenderConfig(DriverConfig[DualPumpBlender], tag="dual_pump_blender"):
    """Two channels of one PWM chip, blended by `commit`.

    The pumps write duties through the `PwmLink` protocol (flyball-linux's
    `pwm`/`fake_pwm`), not a device of their own: the split-range
    arithmetic is the blender's, so it owns both channels directly.
    """

    link: PwmLinkConfig | str  # type: ignore[assignment]
    frequency_hz: Positive = 20_000.0
    dry: PumpLineConfig
    wet: PumpLineConfig
    blend_flow: Positive = 1.0
    supply: SupplyConfig | None = None
    """A starting supply humidity, for a rig with no sensor bound to `dry`/`wet`."""

    def build(self, name: str, label: str | None = None) -> DualPumpBlender:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a PWM chip before building")
        dry = PwmPump(self.link, self.dry.channel, self.frequency_hz, self.dry.deadband)
        wet = PwmPump(self.link, self.wet.channel, self.frequency_hz, self.wet.deadband)
        pumps = DualPumps(PumpPair(dry, wet), MaxFlows(self.dry.max_flow, self.wet.max_flow))
        supply = (
            SupplyHumidities(self.supply.dry, self.supply.wet)
            if self.supply is not None
            else DefaultHumidities
        )
        return DualPumpBlender(name, pumps, supply=supply, blend_flow=self.blend_flow, label=label)

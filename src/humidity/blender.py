"""The dual-pump blender: a composite actuator that mixes a dry and a wet line to a target %RH.

`DualPumpBlender.commit` does the split-range arithmetic once per delivery
(`calculate_wet_fraction`), however many of a new target, a changed supply
reading and a new blend flow arrived together -- one pump write. A demand on
`dry_flow`/`wet_flow` or `dry_effort`/`wet_effort` instead drives the lines
directly, bypassing the arithmetic; `stop` is a command, not a demand.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from flyball.core.device import Device, DeviceSettings, DriverConfig, command
from flyball.core.errors import UnachievableError
from flyball.core.signal import Access, Node, Reading, Sample, Signal, SignalSpec, WriteState
from flyball.core.typing import Normalised, Positive
from flyball_linux.links.pwm import PwmLinkConfig
from pydantic import BaseModel, ConfigDict

from humidity.pumps import (
    Absolute,
    BlendFlow,
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
from humidity.units import EFFORT, FLOW, HUMIDITY, Humidity


class BlenderError(Exception): ...


@dataclass(frozen=True, slots=True, kw_only=True)
class BlenderSettings(DeviceSettings):
    blend: BlendFlow
    """How much air the blend moves: absolute, of the blend's or of the guaranteed maximum."""


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


class Rail(Enum):
    WET = "wet"
    DRY = "dry"

    def __float__(self) -> float:
        return 1.0 if self is Rail.WET else 0.0


def expected_humidity_from_flows(flows: SupplyFlows, humidities: SupplyHumidities) -> float | None:
    total = flows.total
    return (flows * humidities).total / total if total else None


def calculate_wet_fraction(humidities: SupplyHumidities, target: float) -> Normalised | Rail:
    if humidities.wet <= humidities.dry:
        raise SupplyHumiditiesError(humidities)
    if target < humidities.dry:
        return Rail.DRY
    if target > humidities.wet:
        return Rail.WET
    return (target - humidities.dry) / humidities.difference


class DualPumpBlender(Device):
    """Two pumps, blended to a target %RH; also settable directly by flow or by effort.

    Signals: `humidity [W]` the split-range target, limits 0-100; `dry_flow`
    / `wet_flow [RPW]` together, litres/min; `dry_effort` / `wet_effort
    [RPW]` together, 0-1 of full; `blend_flow [RW]` the total flow a
    humidity demand mixes to, a setting; `expected_humidity [RP]` what the
    lines actually deliver. `bound` follows the supply lines' humidity.
    """

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
        self.bind((
            SignalSpec(name="humidity", quantity=HUMIDITY, access=Access.W, limits=(0.0, 100.0)),
            SignalSpec(
                name="dry_flow",
                quantity=FLOW,
                access=Access.RPW,
                limits=(0.0, pumps.dry_max_flow),
                together=frozenset({"wet_flow"}),
            ),
            SignalSpec(
                name="wet_flow",
                quantity=FLOW,
                access=Access.RPW,
                limits=(0.0, pumps.wet_max_flow),
                together=frozenset({"dry_flow"}),
            ),
            SignalSpec(
                name="dry_effort",
                quantity=EFFORT,
                access=Access.RPW,
                limits=(0.0, 1.0),
                together=frozenset({"wet_effort"}),
            ),
            SignalSpec(
                name="wet_effort",
                quantity=EFFORT,
                access=Access.RPW,
                limits=(0.0, 1.0),
                together=frozenset({"dry_effort"}),
            ),
            SignalSpec(name="blend_flow", quantity=FLOW, access=Access.RW),
            SignalSpec(
                name="expected_humidity",
                quantity=HUMIDITY,
                access=Access.RP,
                range=(0.0, 100.0),
                precision=1,
            ),
        ))
        self._pumps = pumps
        self._supply = supply
        self._target = (supply.dry + supply.wet) / 2.0
        self._blend: BlendFlow = Absolute(blend_flow, OnOverdrive.CLAMP)
        self._rail: Literal["low", "high"] | None = None
        self._expected_humidity = expected_humidity_from_flows(pumps.flows, supply)

    @property
    def settings(self) -> BlenderSettings:
        return BlenderSettings(blend=self._blend)

    @property
    def _blend_flow(self) -> float:
        """The total flow the pumps put out: the `blend_flow` readback, honest in every mode."""
        flows = self._pumps.flows
        return flows.dry + flows.wet

    def observe(self, event: Reading | Sample) -> None:
        """A bound supply line published a new humidity: record it, ready for the next `commit`."""
        if not isinstance(event, Reading):
            return  # bound only to leaf signals (dry/wet humidity); a Sample cannot arrive here
        for role, signal in self.bound.items():
            if event.signal is signal:
                self._supply = SupplyHumidities(
                    dry=event.value if role == "dry" else self._supply.dry,
                    wet=event.value if role == "wet" else self._supply.wet,
                )

    def commit(self, time_ns: int) -> Mapping[Signal, WriteState]:
        pending = self.pending
        dry_flow, wet_flow = self.signals["dry_flow"], self.signals["wet_flow"]
        dry_effort, wet_effort = self.signals["dry_effort"], self.signals["wet_effort"]
        humidity, blend_flow = self.signals["humidity"], self.signals["blend_flow"]
        self._rail = None
        if dry_flow in pending or wet_flow in pending:
            self._pumps.set_flows(SupplyFlows(pending[dry_flow], pending[wet_flow]))
        elif dry_effort in pending or wet_effort in pending:
            self._pumps.set_efforts(SupplyEfforts(pending[dry_effort], pending[wet_effort]))
        else:
            self._target = pending.get(humidity, self._target)
            if blend_flow in pending:  # a plain number: an absolute flow, clamped to the lines
                self._blend = Absolute(pending[blend_flow], OnOverdrive.CLAMP)
            self._blend_pumps()
        self._expected_humidity = expected_humidity_from_flows(self._pumps.flows, self._supply)
        states: dict[Signal, WriteState] = {}
        rail = self._rail
        for signal, value in pending.items():
            states[signal] = (
                WriteState(value=value, at_limit=rail)
                if signal is humidity and rail is not None
                else signal.write_state(value)
            )
        self.written.update(states)
        pending.clear()
        return states

    def _blend_pumps(self) -> None:
        """Put the blend on the pumps: the wet fraction for the target, the flow `_blend` says."""
        fraction = calculate_wet_fraction(self._supply, self._target)
        self._rail = (
            ("low" if fraction is Rail.DRY else "high") if isinstance(fraction, Rail) else None
        )
        self._pumps.set_blend(self._blend, float(fraction))

    @command
    def set_blend(self, flow: BlendFlow) -> BlenderSettings:
        """Choose how much air the blend moves.

        An absolute flow (and what to do if the lines cannot give it), a
        fraction of the most the blend can move at this mix, or a fraction of
        the flow guaranteed at every mix. Takes effect at once when blending.
        """
        self._blend = flow
        self._blend_pumps()
        self._expected_humidity = expected_humidity_from_flows(self._pumps.flows, self._supply)
        return self.settings

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        """The setting and every readback, computed from the pumps -- no bus I/O."""
        output = self._pumps.output
        yield Sample(
            self.root,
            time_ns,
            {
                self.signals["dry_flow"]: output.flows.dry,
                self.signals["wet_flow"]: output.flows.wet,
                self.signals["dry_effort"]: output.efforts.dry,
                self.signals["wet_effort"]: output.efforts.wet,
                self.signals["blend_flow"]: self._blend_flow,
                self.signals["expected_humidity"]: self._expected_humidity or 0.0,
            },
        )

    @command
    def stop(self) -> None:
        """Stop both pumps at once, bypassing any pending demand."""
        self._pumps.stop()


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

"""The dual-pump blender: a composite actuator that mixes a dry and a wet line to a target %RH.

A controller drives `humidity`; people run the commands. `set_flows`,
`set_efforts`, `set_fraction` and `stop` drive the lines directly and put
the blender in `flows` mode; a humidity demand (or `set_humidity`) puts it
in `humidity` mode, where `commit` does the split-range arithmetic once per
delivery. `mode` says which demand is in control.
"""

from __future__ import annotations

from typing import Annotated, Literal

from flyball.foundation.device import (
    Access,
    Committable,
    Demand,
    DriverConfig,
    Limit,
    Namespace,
    NoValueError,
    Readout,
    Severity,
    Value,
    command,
    invalid,
    not_applicable,
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
    FixedBlendFlow,
    KeepTotal,
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
    """Which demand is in control of the pumps: the humidity, or the flows. Starts `flows`."""

    HUMIDITY = "humidity", "Blending to a humidity demand"
    FLOWS = "flows", "Flows set directly"


SUPPLY_UNKNOWN = "supply_unknown"
"""The blender's condition while a bound supply sensor has no value: nothing is blended."""

BLEND_FLOW_KEPT = "blend_flow_kept"
"""The blender's event when a `KeepTotal` blend flow resolves on entering `humidity` mode."""

KEEP_FLOOR = 0.01
"""A total flow at or below this share of the guaranteed maximum is taken as stopped: a
`KeepTotal` then uses its `fallback`, since a held total of 0 would blend no air."""

DRY = {"line": "dry"}
WET = {"line": "wet"}
"""Each line's signals share a `line` tag across the tree: `flows.dry`, `efforts.dry`, ..."""


class DualPumpBlender(Committable):
    """Two pumps, blended to a target %RH; also settable directly by flow or by effort.

    A demand on `humidity` puts the blender in `humidity` mode: `commit` does the
    split-range arithmetic once per delivery (`calculate_wet_fraction`),
    however many of a new target, a changed supply reading and a new blend
    flow arrived together -- one pump write. While a bound supply sensor has no
    value (`invalid`, `stale`), nothing is blended: the pumps keep what they
    are doing, `expected_humidity` is `invalid("supply")`, the blender holds
    `supply_unknown`, and `set_humidity` refuses (a `NotReadyError`). `set_flows`, `set_efforts` and
    `stop` drive the lines at once and put it in `flows` mode, so a supply
    reading re-blends only in `humidity` mode. The flows and efforts are demands whose
    readbacks follow whatever is driving the pumps. In `flows` mode `humidity`'s
    readback is what the flows deliver from the supplies now (the model run
    backwards), so a controller in manual tracks what is delivered; with no flow it
    and `blend.wet_fraction` have no value (`not_applicable("no_flow")`).

    The blend is allocated inside the effective `flows.*` limits (a rig file's
    narrowing included), not only the pumps' `max_flow`. An `Absolute` blend flow's
    `raise` refuses only a command run by hand; a blend a humidity demand or a moved
    supply makes always scales. `set_blend` moves the pumps, so it is refused while a
    controller regulates `humidity` (put it in manual first).
    """

    flows = Namespace("flows", "Flows")
    efforts = Namespace("efforts", "Efforts")
    max_flows = Namespace("max_flows", "Max flows")
    humidities = Namespace("humidities", "Flow humidities")
    supply_defaults = Namespace("supply_defaults", "Supply humidities when unbound")

    dry_max_flow = max_flows.config("dry", "Dry max flow", FLOW, tags=DRY)
    wet_max_flow = max_flows.config("wet", "Wet max flow", FLOW, tags=WET)
    dry_supply_default = supply_defaults.config("dry", "Dry line humidity", HUMIDITY, tags=DRY)
    wet_supply_default = supply_defaults.config("wet", "Wet line humidity", HUMIDITY, tags=WET)

    dry_supply = humidities.input("dry", "Dry line humidity", HUMIDITY, default=dry_supply_default)
    wet_supply = humidities.input("wet", "Wet line humidity", HUMIDITY, default=wet_supply_default)

    humidity = Demand("humidity", "Humidity demand", HUMIDITY, limits=(dry_supply, wet_supply))
    """Clamped to what the lines can mix: the supply humidities, as they read now."""
    # Readbacks only: `set_flows`/`set_efforts` are the only way to move these -- see
    # `commit`, which never looks at their `.staged`. Not `access=Access.RPW`'s default for
    # a Demand, so the generic signal editor does not offer a direct write that would be
    # silently accepted and never reach the pumps.
    dry_flow = flows.demand(
        "dry", "Dry pump flow", FLOW, limits=(0.0, dry_max_flow), access=Access.RP, tags=DRY
    )
    wet_flow = flows.demand(
        "wet", "Wet pump flow", FLOW, limits=(0.0, wet_max_flow), access=Access.RP, tags=WET
    )
    dry_effort = efforts.demand(
        "dry", "Dry pump effort", EFFORT, limits=(0.0, 1.0), access=Access.RP, tags=DRY
    )
    wet_effort = efforts.demand(
        "wet", "Wet pump effort", EFFORT, limits=(0.0, 1.0), access=Access.RP, tags=WET
    )

    expected_humidity = Readout(
        "expected_humidity", "Expected humidity", HUMIDITY, range=(0.0, 100.0), precision=1
    )
    mode = Readout("mode", "Mode", vtype=Mode, initial=Mode.FLOWS)
    blend = Namespace("blend", "Blend")
    blend_flow = blend.setting("flow", "Blend flow", vtype=BlendFlow, initial=DefaultBlendFlow)
    # Readback only: `set_fraction` is the only way to move it -- see `commit`, which never
    # looks at its `.staged`.
    wet_fraction = blend.demand(
        "wet_fraction", "Wet fraction", WET_FRACTION, limits=(0.0, 1.0), access=Access.RP
    )

    def __init__(
        self,
        name: str,
        pumps: DualPumps,
        *,
        supply: SupplyHumidities = DefaultHumidities,
        blend_flow: Positive | KeepTotal = 1.0,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self._pumps = pumps
        self._target = (supply.dry + supply.wet) / 2.0
        self._kept: FixedBlendFlow | None = None
        """What a `KeepTotal` resolved to on entering `humidity` mode, held for the episode."""
        if isinstance(blend_flow, KeepTotal):
            fallback = blend_flow.fallback
            self._configured = _scaling(fallback if isinstance(fallback, Absolute) else None)
            setting: BlendFlow = blend_flow
        else:
            self._configured = setting = Absolute(blend_flow, OnOverdrive.CLAMP)
        self.dry_max_flow.push(pumps.dry_max_flow)
        self.wet_max_flow.push(pumps.wet_max_flow)
        self.dry_supply_default.push(supply.dry)
        self.wet_supply_default.push(supply.wet)
        self.blend_flow.push(setting)
        # The pumps as found: every demand has a value from the start.
        self._push_readbacks(flows_in_force=True)

    @property
    def _supply(self) -> SupplyHumidities:
        """The supply lines' humidity now: the bound sensors', or the config's."""
        return SupplyHumidities(dry=self.dry_supply.value, wet=self.wet_supply.value)

    def commit(self, time_ns: int) -> None:
        """A humidity demand takes `humidity` mode; in it, a moved supply re-blends.

        A supply with no value blends nothing, and is not a failed write: the
        pumps keep their blend until the supply reads again.
        """
        if (target := self.humidity.staged) is not None:
            self._target = target
            if self.mode.value is not Mode.HUMIDITY:
                self._kept = None  # a new episode: a KeepTotal resolves again
                self.mode.push(Mode.HUMIDITY, time_ns)
        if self.mode.value is Mode.HUMIDITY:
            try:
                self._blend_pumps(time_ns)
            except NoValueError as error:
                self.set_condition(
                    SUPPLY_UNKNOWN, Severity.WARNING, f"{error}: nothing blended until it reads"
                )
                self.push(time_ns, expected_humidity=invalid("supply"))
        else:
            # A moved supply moves what the same flows deliver.
            expected = self._expected(self._pumps.flows)
            self.push(time_ns, expected_humidity=expected, humidity=expected)

    @property
    def _caps(self) -> MaxFlows:
        """The most each line may be asked for now: the effective `flows.*` limits' tops.

        A rig file's `limits` narrow them below the pumps' `max_flow`; the
        allocator works inside these, the efforts against the pumps' own maxima.
        """
        dry, wet = self.dry_flow.limits, self.wet_flow.limits
        return MaxFlows(
            max(dry[1], 1e-12) if dry is not None else self._pumps.dry_max_flow,
            max(wet[1], 1e-12) if wet is not None else self._pumps.wet_max_flow,
        )

    def _blend_pumps(
        self,
        time_ns: int | None = None,
        blend: BlendFlow | None = None,
        *,
        manual: bool = False,
        rekeep: bool = False,
    ) -> float | None:
        """Put the blend on the pumps: the wet fraction for the target, the flow `blend` says.

        `manual` for a command run by hand (an `Absolute`'s `raise` holds);
        `rekeep` to resolve a `KeepTotal` again although this episode holds one.
        Returns the total a `KeepTotal` resolved to on this call, if one did.

        Raises:
            NoValueError: A bound supply sensor has no value: nothing is blended.
        """
        fraction = calculate_wet_fraction(self._supply, self._target)
        self.clear_condition(SUPPLY_UNKNOWN, message="the supplies read again: blending")
        railed = isinstance(fraction, Limit)
        self.humidity.at_limit = fraction if railed else None
        wet = fraction.fraction if isinstance(fraction, Limit) else fraction
        setting = self.blend_flow.value if blend is None else blend
        return self._set_blend(time_ns, setting, wet, manual=manual, episode=True, rekeep=rekeep)

    def _set_blend(
        self,
        time_ns: int | None,
        setting: BlendFlow,
        wet: float,
        *,
        manual: bool,
        episode: bool = False,
        rekeep: bool = False,
    ) -> float | None:
        """One pump write for a blend, then every readback and the setting at one instant.

        `episode` in `humidity` mode, where a `KeepTotal` is held once resolved.
        Returns the total a `KeepTotal` resolved to on this call, if one did.
        """
        flow, fell_back = self._effective(setting, manual=manual, episode=episode, rekeep=rekeep)
        self._pumps.set_blend(flow, wet, self._caps)
        self._push_readbacks(time_ns, blend=setting, flows_in_force=not episode)
        if fell_back is None or not episode:
            return None  # `set_fraction`'s keep holds the total now, with no episode to report
        return self._report_kept(flow, fell_back=fell_back)

    def _effective(
        self, setting: BlendFlow, *, manual: bool, episode: bool, rekeep: bool
    ) -> tuple[FixedBlendFlow, bool | None]:
        """What the pumps are given for `setting` now.

        With it, for a `KeepTotal` resolved on this call, whether it fell back
        (the pumps were stopped); None when nothing resolved.
        """
        if isinstance(setting, KeepTotal):
            if episode and self._kept is not None and not rekeep:
                return self._kept, None
            total = self._pumps.total_flow
            fell_back = total <= KEEP_FLOOR * self._caps.guaranteed
            kept = (
                _scaling(self._configured if setting.fallback is None else setting.fallback)
                if fell_back
                else Absolute(total, OnOverdrive.CLAMP)
            )
            if episode:
                self._kept = kept
            return kept, fell_back
        if not manual:
            return _scaling(setting), None
        return setting, None

    def _report_kept(self, kept: FixedBlendFlow, *, fell_back: bool) -> float:
        """The event for a resolved `KeepTotal`; returns the total the blend holds."""
        delivered = self._pumps.total_flow
        held = kept.flow if isinstance(kept, Absolute) else delivered
        clamped = delivered < held * (1.0 - 1e-9)
        message = (
            f"the pumps were stopped: humidity mode holds the fallback, {held:.3g} L/min"
            if fell_back
            else f"humidity mode keeps the total flow, {held:.3g} L/min"
        )
        if clamped:
            message += f"; this mix delivers {delivered:.3g} L/min, the most it can"
        self.event(
            BLEND_FLOW_KEPT,
            Severity.WARNING if clamped else Severity.INFO,
            message,
            {"total": held, "fallback": fell_back, "delivered": delivered, "clamped": clamped},
        )
        return held

    def _push_readbacks(
        self,
        time_ns: int | None = None,
        blend: BlendFlow | None = None,
        *,
        flows_in_force: bool = False,
    ) -> None:
        """What the pumps are now doing, on the flow and effort demands, and what it delivers.

        `blend` only when given. `expected_humidity` and `blend.wet_fraction` have
        no value with no flow (`not_applicable("no_flow")`: a chart breaks, nothing
        reads 0); `expected_humidity` none while a supply has none
        (`invalid("supply")`). With `flows_in_force`, `humidity`'s readback is
        `expected_humidity` too: what the flows deliver, not a target.
        """
        output = self._pumps.output
        flows = output.flows
        expected = self._expected(flows)
        self.push(
            time_ns,
            wet_fraction=flows.wet_fraction if flows.total > 0 else not_applicable("no_flow"),
            dry_flow=flows.dry,
            wet_flow=flows.wet,
            dry_effort=output.efforts.dry,
            wet_effort=output.efforts.wet,
            expected_humidity=expected,
            humidity=expected if flows_in_force else None,
            blend_flow=blend,
        )

    def _expected(self, flows: SupplyFlows) -> Value:
        """The humidity `flows` deliver from the supplies now, or why there is none."""
        try:
            supply = self._supply
        except NoValueError:
            return invalid("supply")
        expected = expected_humidity_from_flows(flows, supply)
        return not_applicable("no_flow") if expected is None else expected

    @command(writes=(dry_flow, wet_flow))
    def set_blend(self, blend_flow: BlendFlow) -> None:
        """Choose how much air the blend moves.

        An absolute flow (and what to do if the lines cannot give it), a
        fraction of the most the blend can move at this mix, a fraction of
        the flow guaranteed at every mix, or the total the pumps move when
        the blender enters `humidity` mode (`keep`, with a fallback for when
        they are stopped). Takes effect at once in `humidity` mode, else at
        the next blend. Refused while a controller regulates the humidity:
        put it in manual first.
        """
        if self.mode.value is Mode.HUMIDITY:
            # May refuse (overdrive): then the setting stands.
            self._blend_pumps(blend=blend_flow, manual=True, rekeep=True)
        else:
            self.blend_flow.push(blend_flow)

    @command(mode=Mode.HUMIDITY, interrupts=True)
    def set_humidity(self, humidity: Humidity, blend_flow: BlendFlow | None = None) -> Flow | None:
        """Blend to a humidity at a blend flow, by hand: the controller, if any, goes to manual.

        Either argument left out is filled from its current reading (the
        target's, or `blend.flow`'s) and re-applied. What a controller does
        through the `humidity` demand, done in one go from a program or a form.
        Returns the total flow a `keep` blend flow resolved to on entering
        `humidity` mode (L/min), or null.
        """
        if self.mode.value is not Mode.HUMIDITY:
            self._kept = None  # a new episode: a KeepTotal resolves again
        self._target = humidity
        return self._blend_pumps(blend=blend_flow, manual=True)

    @command(mode=Mode.FLOWS, interrupts=True)
    def set_fraction(self, blend_flow: BlendFlow, wet_fraction: float) -> None:
        """Blend at a wet fraction by hand, at a blend flow.

        Either argument left out is filled from its current reading
        (`blend.flow`'s, or `blend.wet_fraction`'s) and re-applied; with no
        flow the wet fraction has no value, so give it. A `keep` blend flow
        keeps the total the pumps move now.
        """
        self._kept = None
        self._set_blend(None, blend_flow, wet=wet_fraction, manual=True)

    @command(mode=Mode.FLOWS, interrupts=True)
    def set_flows(self, dry: Annotated[Flow, dry_flow], wet: Annotated[Flow, wet_flow]) -> None:
        """Drive each line at a flow. A line left out keeps its current flow."""
        self._kept = None
        self._pumps.set_flows(SupplyFlows(dry, wet))
        self._push_readbacks(flows_in_force=True)

    @command(mode=Mode.FLOWS, interrupts=True)
    def set_efforts(
        self, dry: Annotated[Normalised, dry_effort], wet: Annotated[Normalised, wet_effort]
    ) -> None:
        """Drive each line at an effort, 0-1 of full. A line left out keeps its current effort."""
        self._kept = None
        self._pumps.set_efforts(SupplyEfforts(dry, wet))
        self._push_readbacks(flows_in_force=True)

    @command(mode=Mode.FLOWS, interrupts=True)
    def stop(self) -> None:
        """Stop both pumps at once; a controller driving the target goes to manual."""
        self._kept = None
        self._pumps.stop()
        self._push_readbacks(flows_in_force=True)


class PumpLineConfig(BaseModel):
    """One line's PWM channel and its limits."""

    model_config = ConfigDict(extra="forbid")

    channel: int
    deadband: Normalised = 0.0
    max_flow: Positive


class SupplyConfig(BaseModel):
    """The supply lines' humidity, when not followed from a sensor (`inputs`)."""

    model_config = ConfigDict(extra="forbid")

    dry: Humidity
    wet: Humidity


class KeepBlendFlow(BaseModel):
    """`blend_flow: {keep: true, fallback: 1.0}`: keep the total flow on entering `humidity`.

    The total the pumps move then is held for the episode; `fallback` (L/min,
    scaling) when they are stopped. No bare `keep`: the fallback is always visible.
    """

    model_config = ConfigDict(extra="forbid")

    keep: Literal[True]
    fallback: Positive


def _scaling(flow: FixedBlendFlow | None) -> FixedBlendFlow:
    """`flow` with an `Absolute`'s `raise` made `clamp` (scale); None: 1 L/min, scaling."""
    if flow is None:
        return Absolute(1.0, OnOverdrive.CLAMP)
    if isinstance(flow, Absolute) and flow.on_overdrive is not OnOverdrive.CLAMP:
        return Absolute(flow.flow, OnOverdrive.CLAMP)
    return flow


class DualPumpBlenderConfig(DriverConfig[DualPumpBlender], type="dual_pump_blender"):
    """Two channels of one PWM chip, blended by `commit`.

    The pumps write duties through the `PwmLink` protocol (flyball-linux's
    `pwm`/`fake_pwm`), not a device of their own: the split-range
    arithmetic is the blender's, so it owns both channels directly.
    """

    link: PwmLinkConfig | str  # type: ignore[assignment]
    frequency_hz: Positive = 20_000.0
    dry: PumpLineConfig
    wet: PumpLineConfig
    blend_flow: Positive | KeepBlendFlow = 1.0
    """L/min, scaling to what the mix can move; or `{keep: true, fallback: <L/min>}`."""
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
        blend_flow = (
            KeepTotal(Absolute(self.blend_flow.fallback, OnOverdrive.CLAMP))
            if isinstance(self.blend_flow, KeepBlendFlow)
            else self.blend_flow
        )
        return DualPumpBlender(name, pumps, supply=supply, blend_flow=blend_flow, label=label)

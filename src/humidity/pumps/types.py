from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal, NamedTuple, Self

from flyball.foundation.primitives import Labelled
from flyball.foundation.typing import NonNegative, Normalised, Percent, Positive

from humidity.units import Flow, Humidity


class OnOverdrive(Labelled):
    """How to handle a requested flow change that exceeds the maximum.

    `raise` applies only to a command run by hand (`set_blend`, `set_humidity`,
    `set_fraction`); a blend a controller's humidity demand makes, or a moved
    supply re-blends, always scales, so a controller's write is never refused.
    """

    RAISE = "raise", "Refuse the request"  # raise FlowsOverdrivenError
    CLAMP = "clamp", "Scale both lines down, keeping the mix"  # `SupplyFlows.derated`


@dataclass(frozen=True, slots=True)
class Absolute:
    flow: Flow
    on_overdrive: OnOverdrive = OnOverdrive.RAISE


@dataclass(frozen=True, slots=True)
class OfBlendMax:
    blend_fraction: Normalised = 1.0


@dataclass(frozen=True, slots=True)
class OfGuaranteedMax:
    guaranteed_max_fraction: Normalised = 1.0


type FixedBlendFlow = Absolute | OfBlendMax | OfGuaranteedMax
"""A blend flow that says how much air to move, with no reference to what moves now."""


@dataclass(frozen=True, slots=True)
class KeepTotal:
    """Keep the total flow the pumps were moving when the blender entered `humidity` mode.

    Opt-in; resolved once on each entry into `humidity` mode to
    `Absolute(<total then>, CLAMP)` and held for that episode, never
    re-evaluated per blend. At or below a small floor (pumps stopped),
    `fallback` is used instead; None means the configured blend flow,
    `Absolute(<configured>, CLAMP)`.
    """

    fallback: FixedBlendFlow | None = None
    keep: Literal[True] = True
    """Always true: what tells a `KeepTotal` apart on the wire."""


type BlendFlow = Absolute | OfBlendMax | OfGuaranteedMax | KeepTotal

DefaultBlendFlow = OfGuaranteedMax()


class Blend(NamedTuple):
    flow: BlendFlow
    wet_fraction: Normalised


@dataclass(slots=True, frozen=True)
class DryWet:
    """A value for each line. Arithmetic is elementwise, with another pair or a scalar.

    Operators return the left operand's class; convert with
    [of][humidity.pumps.types.DryWet.of] when the result means something else
    (`SupplyEfforts.of(flows / max_flows)`).
    """

    dry: float
    wet: float

    @classmethod
    def of(cls, pair: DryWet) -> Self:
        """The same two values as this class."""
        return cls(pair.dry, pair.wet)

    @staticmethod
    def _pair(other: object) -> tuple[float, float] | None:
        if isinstance(other, DryWet):
            return other.dry, other.wet
        if isinstance(other, (int, float)) and not isinstance(other, bool):
            return other, other
        return None

    def __iter__(self) -> Iterator[float]:
        yield self.dry
        yield self.wet

    def __truediv__(self, other: DryWet | float) -> Self:
        if (pair := self._pair(other)) is None:
            return NotImplemented
        return type(self)(self.dry / pair[0], self.wet / pair[1])

    def __rtruediv__(self, other: DryWet | float) -> Self:
        if (pair := self._pair(other)) is None:
            return NotImplemented
        return type(self)(pair[0] / self.dry, pair[1] / self.wet)

    def __mul__(self, other: DryWet | float) -> Self:
        if (pair := self._pair(other)) is None:
            return NotImplemented
        return type(self)(self.dry * pair[0], self.wet * pair[1])

    __rmul__ = __mul__

    def __add__(self, other: DryWet | float) -> Self:
        if (pair := self._pair(other)) is None:
            return NotImplemented
        return type(self)(self.dry + pair[0], self.wet + pair[1])

    __radd__ = __add__

    def __sub__(self, other: DryWet | float) -> Self:
        if (pair := self._pair(other)) is None:
            return NotImplemented
        return type(self)(self.dry - pair[0], self.wet - pair[1])

    def __rsub__(self, other: DryWet | float) -> Self:
        if (pair := self._pair(other)) is None:
            return NotImplemented
        return type(self)(pair[0] - self.dry, pair[1] - self.wet)

    def __neg__(self) -> Self:
        return type(self)(-self.dry, -self.wet)

    @property
    def total(self) -> float:
        return self.dry + self.wet

    @property
    def difference(self) -> float:
        return self.wet - self.dry

    @property
    def min(self) -> float:
        return min(self.dry, self.wet)

    @property
    def max(self) -> float:
        return max(self.dry, self.wet)

    def max_with(self, *others: float) -> float:
        """The larger line, or a larger given value."""
        return max(self.dry, self.wet, *others)


@dataclass(slots=True, frozen=True)
class SupplyHumidities(DryWet):
    dry: Humidity
    wet: Humidity


@dataclass(slots=True, frozen=True)
class SupplyFlows(DryWet):
    dry: Flow
    wet: Flow

    @classmethod
    def from_blend(cls, total: NonNegative, wet_fraction: Normalised) -> SupplyFlows:
        return cls(total * (1.0 - wet_fraction), total * wet_fraction)

    @classmethod
    def from_wet_total(cls, wet: NonNegative, total: NonNegative) -> SupplyFlows:
        return cls(total - wet, wet)

    @classmethod
    def from_dry_total(cls, dry: NonNegative, total: NonNegative) -> SupplyFlows:
        return cls(dry, total - dry)

    @property
    def blend(self) -> CurrentBlend:
        return CurrentBlend(self.wet_fraction, self.total)

    @property
    def dry_fraction(self) -> float:
        total = self.total
        return self.dry / total if total > 0 else 0.0

    @property
    def wet_fraction(self) -> float:
        total = self.total
        return self.wet / total if total > 0 else 0.0

    def is_valid(self, max_flows: MaxFlows) -> bool:
        return self.dry <= max_flows.dry and self.wet <= max_flows.wet

    def to_efforts(self, max_flows: MaxFlows) -> SupplyEfforts:
        return SupplyEfforts.of(self / max_flows)

    def to_total_humidity(self, humidities: SupplyHumidities) -> Percent | None:
        total = self.total
        return (self * humidities).total / total if total > 0 else None

    def derated(self, max_flows: MaxFlows) -> SupplyFlows:
        return self / self.to_efforts(max_flows).max_with(1.0)


@dataclass(slots=True, frozen=True)
class SupplyEfforts(DryWet):
    dry: Normalised
    wet: Normalised

    @classmethod
    def shares(cls, wet_fraction: Normalised) -> SupplyEfforts:
        return cls(1.0 - wet_fraction, wet_fraction)

    @property
    def overdriven(self) -> bool:
        return self.dry > 1.0 or self.wet > 1.0

    def to_flows(self, max_flows: MaxFlows) -> SupplyFlows:
        return SupplyFlows.of(self * max_flows)

    def derated(self) -> SupplyEfforts:
        max_effort = self.max
        if max_effort <= 1.0:
            return self
        return self / max_effort


class SupplyDeadbands(DryWet):
    dry: Normalised
    wet: Normalised


DefaultDeadbands = SupplyDeadbands(dry=0.0, wet=0.0)

type PumpsMode = Blend | SupplyFlows | SupplyEfforts


@dataclass(slots=True, frozen=True)
class MaxFlows(DryWet):
    dry: Positive
    wet: Positive

    @property
    def guaranteed(self) -> Positive:
        return self.min

    def flows_at_blend(self, wet_fraction: Normalised) -> SupplyFlows:
        dry_fraction = 1.0 - wet_fraction
        dry_cross = self.dry * wet_fraction
        wet_cross = self.wet * dry_fraction
        if dry_cross <= wet_cross:
            return SupplyFlows(self.dry, dry_cross / dry_fraction)
        return SupplyFlows(wet_cross / wet_fraction, self.wet)

    def flow_at_blend(self, wet_fraction: Normalised) -> NonNegative:
        return self.flows_at_blend(wet_fraction).total

    def to_efforts(self, flows: SupplyFlows) -> SupplyEfforts:
        return SupplyEfforts.of(flows / self)

    def to_flows(self, efforts: SupplyEfforts) -> SupplyFlows:
        return SupplyFlows.of(efforts * self)

    def efforts_at_blend(self, wet_fraction: Normalised) -> SupplyEfforts:
        return self.to_efforts(self.flows_at_blend(wet_fraction))


MaxFlowsDefault = MaxFlows(dry=1.0, wet=1.0)


@dataclass(slots=True, frozen=True)
class PumpState:
    effort: Normalised
    flow: Flow


@dataclass(slots=True, frozen=True)
class PumpsState:
    efforts: SupplyEfforts
    flows: SupplyFlows


@dataclass(slots=True, frozen=True)
class CurrentBlend:
    wet_fraction: Normalised
    flow: Flow


@dataclass(slots=True, frozen=True)
class PumpsLimits:
    """What the built pumps can do. Config: fixed once built."""

    max_flows: MaxFlows
    guaranteed_max_flow: Positive
    units: str | None


@dataclass(slots=True, frozen=True)
class PumpsView:
    """Limits and state at one instant."""

    limits: PumpsLimits
    state: PumpsState

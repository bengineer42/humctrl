from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import NamedTuple, Self

from flyball.core.typing import NonNegative, Normalised, Percent, Positive
from flyball.core.utils import Labelled


class OnOverdrive(Labelled):
    """How to handle a requested flow change that exceeds the maximum."""

    RAISE = "raise", "Refuse the request"  # raise FlowsOverdrivenError
    CLAMP = "clamp", "Clamp to the maximum"  # clamp to the maximum flow


@dataclass(frozen=True, slots=True)
class Absolute:
    value: NonNegative
    on_overdrive: OnOverdrive = OnOverdrive.RAISE


@dataclass(frozen=True, slots=True)
class OfBlendMax:
    value: Normalised = 1.0


@dataclass(frozen=True, slots=True)
class OfGuaranteedMax:
    value: Normalised = 1.0


type BlendFlow = Absolute | OfBlendMax | OfGuaranteedMax

DefaultBlendFlow = OfGuaranteedMax()


class Blend(NamedTuple):
    flow: BlendFlow
    wet_fraction: Normalised


_NUMERIC = (float, int)


def parse_values(values: object) -> tuple[float, float] | None:
    """The pair ``values`` describes, or None if it describes none.

    Takes ``object`` rather than :data:`DryWetLike`: rejecting what the alias
    does not admit is the whole job, and a narrower annotation would make the
    final ``return None`` unreachable.
    """
    if isinstance(values, _NUMERIC):
        return values, values
    if isinstance(values, DryWet):
        return values.dry, values.wet
    if isinstance(values, (tuple, list)) and len(values) == 2:
        return values[0], values[1]
    return None


def require_values(values: object, name: str) -> tuple[float, float]:
    """The pair ``values`` describes.

    Raises:
        TypeError: If ``values`` describes no pair. A wrong kind of thing, not
            a bad value -- ``ValueError`` is mapped to a 422 by the server,
            which would report a programming error as a client one.
    """
    if (pair := parse_values(values)) is None:
        raise TypeError(
            f"{name} must be a number, a pair of numbers, or a DryWet, got {type(values).__name__}"
        )
    return pair


class DryWetOps:
    __slots__ = ()
    dry: float
    wet: float

    def __init__(self, dry: float, wet: float) -> None: ...

    @classmethod
    def of(cls, values: DryWetLike) -> Self:
        return cls(*require_values(values, cls.__name__))  # pyright: ignore[reportArgumentType]

    @classmethod
    def of_dry_wet(cls, values: DryWetOps) -> Self:
        return cls(values.dry, values.wet)  # pyright: ignore[reportArgumentType]

    def __iter__(self) -> Iterator[float]:
        yield self.dry
        yield self.wet

    def __truediv__(self, other: DryWetLike) -> Self:
        if (pair := parse_values(other)) is None:
            return NotImplemented
        return type(self)(self.dry / pair[0], self.wet / pair[1])  # pyright: ignore[reportArgumentType]

    def __rtruediv__(self, other: DryWetLike) -> Self:
        if (pair := parse_values(other)) is None:
            return NotImplemented
        return type(self)(pair[0] / self.dry, pair[1] / self.wet)  # pyright: ignore[reportArgumentType]

    def __mul__(self, other: DryWetLike) -> Self:
        if (pair := parse_values(other)) is None:
            return NotImplemented
        return type(self)(self.dry * pair[0], self.wet * pair[1])  # pyright: ignore[reportArgumentType]

    __rmul__ = __mul__

    def __add__(self, other: DryWetLike) -> Self:
        if (pair := parse_values(other)) is None:
            return NotImplemented
        return type(self)(self.dry + pair[0], self.wet + pair[1])  # pyright: ignore[reportArgumentType]

    __radd__ = __add__

    def __sub__(self, other: DryWetLike) -> Self:
        if (pair := parse_values(other)) is None:
            return NotImplemented
        return type(self)(self.dry - pair[0], self.wet - pair[1])  # pyright: ignore[reportArgumentType]

    def __rsub__(self, other: DryWetLike) -> Self:
        if (pair := parse_values(other)) is None:
            return NotImplemented
        return type(self)(pair[0] - self.dry, pair[1] - self.wet)  # pyright: ignore[reportArgumentType]

    def __neg__(self) -> Self:
        return type(self)(-self.dry, -self.wet)  # pyright: ignore[reportArgumentType]

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

    def max_with(self, *args) -> float:
        """Return the maximum of the dry and wet values, or the maximum of the given arguments."""
        return max(self.dry, self.wet, *args)


@dataclass(slots=True, frozen=True)
class DryWet(DryWetOps):
    dry: float
    wet: float


@dataclass(slots=True)
class MutDryWet(DryWetOps):
    dry: float
    wet: float


type DryWetLike = DryWetOps | tuple[float, float] | list[float] | float


@dataclass(slots=True, frozen=True)
class SupplyHumidities(DryWet):
    dry: RelativeHumidity
    wet: RelativeHumidity


@dataclass(slots=True)
class MutSupplyHumidities(MutDryWet):
    pass


type SupplyHumiditiesLike = (
    SupplyHumidities | MutSupplyHumidities | DryWetOps | tuple[Percent, Percent] | list[Percent]
)

DefaultHumidities = SupplyHumidities(dry=0.0, wet=100.0)


@dataclass(slots=True, frozen=True)
class SupplyFlows(DryWet):
    dry: NonNegative
    wet: NonNegative

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

    def is_valid(self, max_flows: MaxFlowsLike) -> bool:
        dry_max, wet_max = require_values(max_flows, "max_flows")  # pyright: ignore[reportGeneralTypeIssues]
        return self.dry <= dry_max and self.wet <= wet_max

    def to_efforts(self, max_flows: MaxFlowsLike) -> SupplyEfforts:
        return SupplyEfforts.of(self / max_flows)

    def to_total_humidity(self, humidities: SupplyHumiditiesLike) -> Percent | None:
        total = self.total
        return (self * humidities).total / total if total > 0 else None

    def derated(self, max_flows: MaxFlowsLike) -> SupplyFlows:
        return self / self.to_efforts(max_flows).max_with(1.0)


type SupplyFlowsLike = (
    SupplyFlows | DryWetOps | tuple[NonNegative, NonNegative] | list[NonNegative] | NonNegative
)


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

    def to_flows(self, max_flows: MaxFlowsLike) -> SupplyFlows:
        return SupplyFlows.of_dry_wet(self * max_flows)

    def derated(self) -> SupplyEfforts:
        max_effort = self.max
        if max_effort <= 1.0:
            return self
        return self / max_effort


type SupplyEffortsLike = (
    SupplyEfforts | DryWetOps | tuple[Normalised, Normalised] | Normalised | list[Normalised]
)


class SupplyDeadbands(DryWet):
    dry: Normalised
    wet: Normalised


type SupplyDeadbandsLike = (
    SupplyDeadbands | DryWetOps | tuple[Normalised, Normalised] | Normalised | list[Normalised]
)

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

    def to_efforts(self, flows: SupplyFlowsLike) -> SupplyEfforts:
        return SupplyEfforts.of_dry_wet(flows / self)  #

    def to_flows(self, efforts: SupplyEffortsLike) -> SupplyFlows:
        return SupplyFlows.of_dry_wet(efforts * self)

    def efforts_at_blend(self, wet_fraction: Normalised) -> SupplyEfforts:
        return self.to_efforts(self.flows_at_blend(wet_fraction))


type MaxFlowsLike = MaxFlows | DryWetOps | tuple[Positive, Positive] | Positive


MaxFlowsDefault = MaxFlows(dry=1.0, wet=1.0)


@dataclass(slots=True, frozen=True)
class PumpState:
    effort: Normalised
    flow: NonNegative


@dataclass(slots=True, frozen=True)
class PumpsState:
    efforts: SupplyEfforts
    flows: SupplyFlows


@dataclass(slots=True, frozen=True)
class CurrentBlend:
    wet_fraction: Normalised
    flow: NonNegative


@dataclass(slots=True, frozen=True)
class PumpsSpec:
    max_flows: MaxFlows
    guaranteed_max_flow: Positive
    units: str | None


@dataclass(slots=True, frozen=True)
class PumpsView:
    flows: SupplyFlows
    efforts: SupplyEfforts
    max_flows: MaxFlows
    guaranteed_max_flow: Positive
    units: str | None

    @classmethod
    def of(cls, spec: PumpsSpec, output: PumpsState) -> PumpsView:
        return cls(
            flows=output.flows,
            efforts=output.efforts,
            max_flows=spec.max_flows,
            guaranteed_max_flow=spec.guaranteed_max_flow,
            units=spec.units,
        )
